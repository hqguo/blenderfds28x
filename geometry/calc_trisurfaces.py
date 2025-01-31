"""BlenderFDS, algorithms for triangulated surfaces."""

from time import time
from math import floor, ceil

import bpy, bmesh, mathutils, logging

from ..types import BFException
from . import utils

log = logging.getLogger(__name__)

# Get triangulated surface


def get_trisurface(
    context, ob, scale_length, check=True, terrain=False
) -> "mas, verts, faces":
    """Get triangulated surface from object in xbs format."""
    log.debug(ob.name)
    mas = _get_materials(context, ob)
    bm = utils.get_object_bmesh(
        context=context, ob=ob, world=True, triangulate=True, lookup=False
    )
    if check:
        _check_bm_quality(context, ob, bm, protect=True)
    # Extract verts and faces from bmesh
    verts, faces = list(), list()
    for v in bm.verts:
        co = v.co
        verts.append((co.x * scale_length, co.y * scale_length, co.z * scale_length))
    for f in bm.faces:
        v = f.verts
        faces.append(
            (v[0].index + 1, v[1].index + 1, v[2].index + 1, f.material_index + 1)
        )  # FDS index start from 1, not 0
    # Clean up
    bm.free()
    return mas, verts, faces


def _get_materials(context, ob):
    """Get referenced ob materials from slots."""
    mas, material_slots = list(), ob.material_slots
    if len(material_slots) == 0:
        raise BFException(ob, "No referenced SURF, add at least one Material")
    for material_slot in material_slots:
        ma = material_slot.material
        if not ma:
            raise BFException(
                ob, "No referenced SURF, fill empty slot with at least one Material"
            )
        if not ma.bf_surf_export:
            raise BFException(ob, f"Referenced SURF <{ma.name}> is not exported")
        mas.append(ma.name)
    return mas


# Check quality


def check_geom_quality(context, ob, protect):
    """Check that Object is a closed orientable manifold,
    with no degenerate geometry."""
    bm = utils.get_object_bmesh(
        context=context, ob=ob, world=False, triangulate=True, lookup=True
    )
    _check_bm_quality(context, ob, bm, protect)
    bm.free()


def _check_bm_quality(context, ob, bm, protect):
    """Check that bmesh is a closed orientable manifold, with no degenerate geometry."""
    epsilon_len = context.scene.bf_config_min_edge_length
    epsilon_area = context.scene.bf_config_min_face_area
    _check_bm_manifold_verts(context, ob, bm, epsilon_len, epsilon_area, protect)
    _check_bm_manifold_edges(context, ob, bm, epsilon_len, epsilon_area, protect)
    _check_bm_degenerate_edges(context, ob, bm, epsilon_len, epsilon_area, protect)
    _check_bm_degenerate_faces(context, ob, bm, epsilon_len, epsilon_area, protect)
    _check_bm_loose_vertices(context, ob, bm, epsilon_len, epsilon_area, protect)
    _check_bm_duplicate_vertices(context, ob, bm, epsilon_len, epsilon_area, protect)
    _check_bm_normals(context, ob, bm, epsilon_len, epsilon_area, protect)


def _check_bm_manifold_verts(context, ob, bm, epsilon_len, epsilon_area, protect):
    """Check manifold vertices."""
    bad_verts = list()
    for vert in bm.verts:
        if not vert.is_manifold:
            bad_verts.append(vert)
    if bad_verts:
        msg = f"Non manifold vertices detected ({len(bad_verts)} vertices)."
        _raise_bad_geometry(context, ob, bm, msg, protect, bad_verts=bad_verts)


def _check_bm_manifold_edges(context, ob, bm, epsilon_len, epsilon_area, protect):
    """Check manifold edges, each edge should join two faces, no more no less."""
    bad_edges = list()
    for edge in bm.edges:
        if not edge.is_manifold:
            bad_edges.append(edge)
    if bad_edges:
        msg = f"Non manifold or open geometry detected ({len(bad_edges)} edges)."
        _raise_bad_geometry(context, ob, bm, msg, protect, bad_edges=bad_edges)


def _check_bm_normals(context, ob, bm, epsilon_len, epsilon_area, protect):
    """Check normals, adjoining faces should have normals in the same directions."""
    bad_edges = list()
    for edge in bm.edges:
        if not edge.is_contiguous:
            bad_edges.append(edge)
    if bad_edges:
        msg = f"Inconsistent face normals detected ({len(bad_edges)} edges)."
        _raise_bad_geometry(context, ob, bm, msg, protect, bad_edges=bad_edges)
    if not protect:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)


def _check_bm_degenerate_edges(context, ob, bm, epsilon_len, epsilon_area, protect):
    """Check no degenerate edges, zero lenght edges."""
    bad_edges = list()
    for edge in bm.edges:
        if edge.calc_length() <= epsilon_len:
            bad_edges.append(edge)
    if bad_edges:
        msg = f"Too short edges detected ({len(bad_edges)} edges)."
        _raise_bad_geometry(context, ob, bm, msg, protect, bad_edges=bad_edges)


def _check_bm_degenerate_faces(
    context, ob, bm, epsilon_len, epsilon_area, protect=True
):
    """Check degenerate faces, zero area faces."""
    bad_faces = list()
    for face in bm.faces:
        if face.calc_area() <= epsilon_area:
            bad_faces.append(face)
    if bad_faces:
        msg = f"Too small area faces detected ({len(bad_faces)} faces)."
        _raise_bad_geometry(context, ob, bm, msg, protect, bad_faces=bad_faces)


def _check_bm_loose_vertices(context, ob, bm, epsilon_len, epsilon_area, protect):
    """Check loose vertices, vertices that have no connectivity."""
    bad_verts = list()
    for vert in bm.verts:
        if not bool(vert.link_edges):
            bad_verts.append(vert)
    if bad_verts:
        msg = f"Loose vertices detected ({len(bad_verts)} vertices)."
        _raise_bad_geometry(context, ob, bm, msg, protect, bad_verts=bad_verts)


def _check_bm_duplicate_vertices(context, ob, bm, epsilon_len, epsilon_area, protect):
    """Check duplicate vertices."""
    bad_verts = list()
    size = len(bm.verts)
    kd = mathutils.kdtree.KDTree(size)  # create a kd-tree from a mesh
    for i, vert in enumerate(bm.verts):
        kd.insert(vert.co, i)
    kd.balance()
    for vert in bm.verts:
        vert_group = list()
        for (_, i, _) in kd.find_range(vert.co, epsilon_len):
            vert_group.append(i)
        if len(vert_group) > 1:
            for i in vert_group:
                bad_verts.append(bm.verts[i])
    if bad_verts:
        msg = f"Duplicate vertices detected ({len(bad_verts)} vertices)."
        _raise_bad_geometry(context, ob, bm, msg, protect, bad_verts=bad_verts)


# Check intersections


def check_intersections(context, ob, other_obs=None, protect=True):
    """Check ob self-intersection and intersection with other_obs."""
    log.debug(f"Check intersections in Object <{ob.name}>")
    if context.object:
        bpy.ops.object.mode_set(mode="OBJECT")
    epsilon_len = context.scene.bf_config_min_edge_length
    bad_faces = list()
    bm = utils.get_object_bmesh(context=context, ob=ob, lookup=True)
    tree = mathutils.bvhtree.BVHTree.FromBMesh(bm, epsilon=epsilon_len)
    # Get self-intersections
    bad_faces.extend(_get_bm_intersected_faces(bm, tree, tree))
    # Get intersections
    for other_ob in other_obs or tuple():
        matrix = (
            ob.matrix_world.inverted() @ other_ob.matrix_world
        )  # Blender 2.80 matrix multiplication
        other_bm = utils.get_object_bmesh(
            context=context, ob=ob, matrix=matrix, lookup=True
        )
        other_tree = mathutils.bvhtree.BVHTree.FromBMesh(other_bm, epsilon=epsilon_len)
        other_bm.free()
        bad_faces.extend(_get_bm_intersected_faces(bm, tree, other_tree))
    # Raise
    if bad_faces:
        msg = "Intersection detected."
        _raise_bad_geometry(context, ob, bm, msg, protect, bad_faces=bad_faces)


def _get_bm_intersected_faces(bm, tree, other_tree):
    """Get intersected faces between trees."""
    overlap = tree.overlap(other_tree)
    if overlap:
        ifaces = {i_pair[0] for i_pair in overlap}
        return [bm.faces[iface] for iface in ifaces]
    return list()


# Raise bad geometry


def _raise_bad_geometry(
    context, ob, bm, msg, protect, bad_verts=None, bad_edges=None, bad_faces=None
):
    """Select bad elements, show them, raise BFException."""
    if protect:
        raise BFException(ob, msg)
    # Deselect all in bmesh
    for vert in bm.verts:
        vert.select = False
    bm.select_flush(False)
    # Select bad elements
    select_type = None
    if bad_faces:
        select_type = "FACE"
        for b in bad_faces:
            b.select = True
    if bad_edges:
        select_type = "EDGE"
        for b in bad_edges:
            b.select = True
    if bad_verts:
        select_type = "VERT"
        for b in bad_verts:
            b.select = True
    # Remove applied modifiers, but keep it relative
    ob.modifiers.clear()
    # Apply bm to ob
    bm.to_mesh(ob.data)
    bm.free()
    # Select object and go to edit mode
    if context.object:
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)  # Blender 2.80
    context.view_layer.objects.active = ob  # Blender 2.80
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type=select_type)
    raise BFException(ob, msg)
