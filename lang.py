# BlenderFDS, an open tool for the NIST Fire Dynamics Simulator
# Copyright (C) 2013  Emanuele Gissi, http://www.blenderfds.org
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTIBILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import re, os.path, time, sys, logging
from collections import OrderedDict

import bpy
from bpy.types import (
    bpy_struct,
    PropertyGroup,
    UIList,
    Operator,
    Object,
    Scene,
    Material,
    Collection,
)
from bpy.props import (
    BoolProperty,
    FloatProperty,
    IntProperty,
    IntVectorProperty,
    StringProperty,
    PointerProperty,
    EnumProperty,
    CollectionProperty,
)
from . import geometry
from .types import (
    BFException,
    BFParam,
    BFParamXB,
    BFParamXYZ,
    BFParamPB,
    BFParamStr,
    BFParamFYI,
    BFParamOther,
    BFNamelist,
    BFNamelistSc,
    BFNamelistOb,
    BFNamelistMa,
    FDSParam,
    FDSNamelist,
    FDSCase,
)
from .config import default_mas
from . import gis
from . import utils

log = logging.getLogger(__name__)

# Collections

bf_namelists = list()
bf_params = list()
bl_classes = list()  # list of all Blender classes that need registering
bf_classes = list()  # list of all BlenderFDS classes that need registering

bf_namelists_by_cls = dict()  # dict of all BFNamelist classes by cls name
bf_namelists_by_fds_label = dict()  # dict of all BFNamelist classes by fds_label


def subscribe(cls):
    """Subscribe class to related collection."""
    if issubclass(cls, BFNamelist):
        bf_namelists.append(cls)
        bf_namelists_by_cls[cls.__name__] = cls
        if cls.fds_label:
            bf_namelists_by_fds_label[cls.fds_label] = cls
    elif issubclass(cls, BFParam):
        bf_params.append(cls)
    elif issubclass(cls, bpy_struct):
        bl_classes.append(cls)
    else:
        bf_classes.append(cls)
    return cls


# PropertyGroup and UIList
# The PG properties should always be: bf_export, name


@subscribe
class WM_PG_bf_other(PropertyGroup):
    bf_export: BoolProperty(name="Export", default=True)
    name: StringProperty(name="Name")


@subscribe
class WM_UL_bf_other_items(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data):
        col = layout.column()
        col.active = item.bf_export
        col.prop(item, "name", text="", emboss=False, icon_value=icon)
        col = layout.column()
        col.prop(item, "bf_export", text="")


@subscribe
class WM_PG_bf_filepaths(PropertyGroup):
    bf_export: BoolProperty(name="Export", default=False)
    name: StringProperty(name="Name", subtype="FILE_PATH")


@subscribe
class WM_UL_bf_filepaths_items(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data):
        col = layout.column()
        col.active = item.bf_export
        col.prop(item, "name", text="", emboss=False, icon_value=icon)
        col = layout.column()
        col.prop(item, "bf_export", text="")


# Case config


@subscribe
class SP_config_directory(BFParam):
    label = "Case Directory"
    description = "Destination directory for exported case"
    bpy_type = Scene
    bpy_idname = "bf_config_directory"
    bpy_prop = StringProperty
    bpy_other = {"subtype": "DIR_PATH", "maxlen": 1024}

    def check(self, context):
        value = self.element.bf_config_directory
        if value and not os.path.exists(bpy.path.abspath(value)):
            raise BFException(self, "Case directory path not existing")


@subscribe
class SP_config_text(BFParam):
    label = "Free Text"
    description = "Internal free text, included verbatim"
    bpy_type = Scene
    bpy_idname = "bf_config_text"
    bpy_prop = PointerProperty
    bpy_other = {"type": bpy.types.Text}


@subscribe
class SN_config(BFNamelistSc):
    label = "FDS Case Config"

    def draw(self, context, layout):
        sc = self.element
        col = layout.column()
        col.prop(sc, "bf_config_directory")
        row = col.row(align=True)
        row.prop(sc, "bf_config_text")
        row.operator("scene.bf_show_text", text="", icon="GREASEPENCIL")  # TODO port to draw_operators


# Config origin geolocation


@subscribe
class SP_crs(BFParam):
    label = "Coordinate Reference System"
    description = "Coordinate reference system"
    bpy_type = Scene
    bpy_idname = "bf_crs"
    bpy_prop = EnumProperty
    bpy_default = "LonLat"
    bpy_other = {
        "items": (
            (
                "LonLat",
                "WGS84 Lon/Lat",
                "Decimal degrees longitude/latitude geographic coordinates\nwith WGS84 coordinate reference system",
            ),
            (
                "UTM",
                "WGS84 UTM",
                "Universal Transverse Mercator projected coordinates\nwith WGS84 coordinate reference system",
            ),
        )
    }


def update_lonlat(self, context):
    sc = context.scene
    utm = gis.LonLat(sc.bf_lon, sc.bf_lat).to_UTM()
    sc["bf_utm_zn"] = utm.zn  # avoid triggering another update
    sc["bf_utm_ne"] = utm.ne
    sc["bf_utm_easting"] = utm.easting
    sc["bf_utm_northing"] = utm.northing


def update_utm(self, context):
    sc = context.scene
    lonlat = gis.UTM(
        sc.bf_utm_zn, sc.bf_utm_ne, sc.bf_utm_easting, sc.bf_utm_northing
    ).to_LonLat()
    sc["bf_lon"] = lonlat.lon  # avoid triggering another update
    sc["bf_lat"] = lonlat.lat


@subscribe
class SP_geoname(BFParam):
    label = "Origin Geoname"
    description = "Origin location geographic name"
    bpy_type = Scene
    bpy_idname = "bf_geoname"
    bpy_prop = StringProperty
    bpy_default = "Monte di Portofino, Genova, Italy"


@subscribe
class SP_lon(BFParam):
    label = "Origin Longitude"
    description = "Longitude (WGS84, EPSG:4326) of world origin in decimal degrees"
    bpy_type = Scene
    bpy_idname = "bf_lon"
    bpy_prop = FloatProperty
    bpy_default = 9.1688903
    bpy_other = {"min": -180.0, "max": 180.0, "precision": 9, "update": update_lonlat}


@subscribe
class SP_lat(BFParam):
    label = "Origin Latitude"
    description = "Latitude (WGS84, EPSG:4326) of world origin in decimal degrees"
    bpy_type = Scene
    bpy_idname = "bf_lat"
    bpy_prop = FloatProperty
    bpy_default = 44.3267618
    bpy_other = {"min": -80.0, "max": 84.0, "precision": 9, "update": update_lonlat}


@subscribe
class SP_utm_zn(BFParam):
    label = "Origin UTM Zone Number"
    description = "UTM Zone Number (WGS84) of world origin"
    bpy_type = Scene
    bpy_idname = "bf_utm_zn"
    bpy_prop = IntProperty
    bpy_default = 32
    bpy_other = {"min": 1, "max": 60, "update": update_utm}


@subscribe
class SP_utm_ne(BFParam):
    label = "Origin UTM Northern Emisphere"
    description = "UTM northern emisphere (WGS84) of world origin"
    bpy_type = Scene
    bpy_idname = "bf_utm_ne"
    bpy_prop = BoolProperty
    bpy_default = True  # Monte Fasce, Genova, Italy
    bpy_other = {"update": update_utm}


@subscribe
class SP_utm_easting(BFParam):
    label = "Origin UTM Easting"
    description = "UTM easting (WGS84) of world origin"
    bpy_type = Scene
    bpy_idname = "bf_utm_easting"
    bpy_prop = FloatProperty
    bpy_default = 513466.0
    bpy_other = {"unit": "LENGTH", "update": update_utm, "min": 0, "max": 1000000}


@subscribe
class SP_utm_northing(BFParam):
    label = "Origin UTM Northing"
    description = "UTM northing (WGS84) of world origin"
    bpy_type = Scene
    bpy_idname = "bf_utm_northing"
    bpy_prop = FloatProperty
    bpy_default = 4908185.0
    bpy_other = {"unit": "LENGTH", "update": update_utm, "min": 0, "max": 10000000}


@subscribe
class SP_elevation(BFParam):
    label = "Origin Elevation"
    description = "Elevation of world origin"
    bpy_type = Scene
    bpy_idname = "bf_elevation"
    bpy_prop = FloatProperty
    bpy_default = 610.0
    bpy_other = {"unit": "LENGTH", "precision": 4}


@subscribe
class SN_config_geoloc(BFNamelistSc):
    label = "Origin Geolocation"

    def draw(self, context, layout):
        sc = self.element
        col = layout.column()
        row = col.row()
        row.prop(sc, "bf_crs", text="Coordinate Ref Sys")
        url = gis.LonLat(lon=sc.bf_lon, lat=sc.bf_lat).to_url()
        row.operator("wm.url_open", text="", icon="URL").url = url

        sub = col.column(align=True)
        sub.prop(sc, "bf_geoname", text="Origin Geoname")
        if sc.bf_crs == "LonLat":
            sub.prop(sc, "bf_lon", text="Longitude")
            sub.prop(sc, "bf_lat", text="Latitude")
        else:
            row = sub.row(align=True)
            row.prop(sc, "bf_utm_zn", text="UTM Zone")
            row.prop(sc, "bf_utm_ne", text="N", toggle=1)
            row.prop(sc, "bf_utm_ne", text="S", toggle=1, invert_checkbox=True)
            sub.prop(sc, "bf_utm_easting", text="Easting")
            sub.prop(sc, "bf_utm_northing", text="Northing")
        sub.prop(sc, "bf_elevation", text="Elevation")


# Config sizes


@subscribe
class SP_config_min_edge_length(BFParam):
    label = "Min Edge Length"
    description = "Min allowed edge length"
    bpy_type = Scene
    bpy_idname = "bf_config_min_edge_length"
    bpy_prop = FloatProperty
    bpy_default = 1e-05
    bpy_other = {"unit": "LENGTH"}


@subscribe
class SP_config_min_face_area(BFParam):
    label = "Min Face Area"
    description = "Min allowed face area"
    bpy_type = Scene
    bpy_idname = "bf_config_min_face_area"
    bpy_prop = FloatProperty
    bpy_default = 1e-05
    bpy_other = {"unit": "AREA"}


@subscribe
class SP_config_default_voxel_size(BFParam):
    label = "Voxel/Pixel Size"
    description = "Default voxel/pixel resolution"
    bpy_type = Scene
    bpy_idname = "bf_default_voxel_size"
    bpy_prop = FloatProperty
    bpy_default = 0.1
    bpy_other = {"unit": "LENGTH", "step": 1.0, "precision": 3}


@subscribe
class SN_config_sizes(BFNamelistSc):
    label = "Default Sizes and Thresholds"

    def draw(self, context, layout):
        sc = self.element
        col = layout.column()
        col.prop(sc, "bf_default_voxel_size")
        col.prop(sc, "bf_config_min_edge_length")
        col.prop(sc, "bf_config_min_face_area")

# FIXME cache geometry

# Config units


@subscribe
class SN_config_units(BFNamelistSc):
    label = "Units"

    def draw(self, context, layout):
        sc = self.element
        unit = sc.unit_settings
        col = layout.column()
        col.prop(unit, "system")
        col = col.column()
        col.enabled = unit.system != "NONE"
        col.prop(unit, "scale_length")
        col.prop(unit, "use_separate")
        col.prop(unit, "length_unit", text="Length")
        # col.prop(unit, "mass_unit", text="Mass")  # Unused
        # col.prop(unit, "time_unit", text="Time")  # Unused


# HEAD/TAIL


@subscribe
class SP_HEAD_CHID(BFParam):
    label = "CHID"
    description = "Case identificator, also used as case filename"
    fds_label = "CHID"
    bf_other = {"copy_protect": True}
    bpy_type = Scene
    bpy_idname = "name"

    def check(self, context):
        value = self.element.name
        if value and bpy.path.clean_name(value) != value:
            raise BFException(self, "Illegal characters in case filename")


@subscribe
class SP_HEAD_TITLE(BFParamFYI):
    label = "TITLE"
    description = "Case description"
    fds_label = "TITLE"
    bpy_type = Scene
    bpy_idname = "bf_head_title"
    bpy_other = {"maxlen": 64}


@subscribe
class SN_HEAD(BFNamelistSc):
    label = "HEAD"
    description = "Case header"
    enum_id = 3001
    fds_label = "HEAD"
    bpy_export = "bf_head_export"
    bpy_export_default = True
    bf_params = SP_HEAD_CHID, SP_HEAD_TITLE
    maxlen = 0


@subscribe
class SN_TAIL(BFNamelistSc):  # for importing only
    label = "TAIL"
    description = "Case closing"
    enum_id = 3010
    fds_label = "TAIL"

    def to_fds_namelist(self, context):
        pass

    def from_fds(self, context, fds_params):
        pass


# TIME


@subscribe
class SP_TIME_setup_only(BFParam):
    label = "Smokeview Geometry Setup"
    description = "Set Smokeview to setup only geometry"
    bpy_type = Scene
    bpy_idname = "bf_time_setup_only"
    bpy_prop = BoolProperty
    bpy_default = False

    def to_fds_param(self, context):
        if self.element.bf_time_setup_only:
            return FDSParam(
                label="T_END", values=(0.0,), msg="Smokeview setup only", precision=1
            )


@subscribe
class SP_TIME_T_BEGIN(BFParam):
    label = "T_BEGIN [s]"
    description = "Simulation starting time"
    fds_label = "T_BEGIN"
    fds_default = 0.0
    bpy_type = Scene
    bpy_idname = "bf_time_t_begin"
    bpy_prop = FloatProperty
    bpy_other = {"step": 100.0, "precision": 1}  # "unit": "TIME", not working

    @property
    def exported(self):
        return super().exported and not self.element.bf_time_setup_only


@subscribe
class SP_TIME_T_END(BFParam):
    label = "T_END [s]"
    description = "Simulation ending time"
    fds_label = "T_END"
    bpy_type = Scene
    bpy_idname = "bf_time_t_end"
    bpy_prop = FloatProperty
    bpy_default = 1.0
    bpy_other = {"step": 100.0, "precision": 1}  # "unit": "TIME", not working

    @property
    def exported(self):
        return super().exported and not self.element.bf_time_setup_only


@subscribe
class SP_TIME_other(BFParamOther):
    bpy_type = Scene
    bpy_idname = "bf_time_other"
    bpy_pg = WM_PG_bf_other
    bpy_ul = WM_UL_bf_other_items


@subscribe
class SN_TIME(BFNamelistSc):
    label = "TIME"
    description = "Simulation time settings"
    enum_id = 3002
    fds_label = "TIME"
    bpy_export = "bf_time_export"
    bpy_export_default = True
    bf_params = SP_TIME_T_BEGIN, SP_TIME_T_END, SP_TIME_setup_only, SP_TIME_other
    maxlen = 0


# MISC


@subscribe
class SP_MISC_FYI(BFParamFYI):
    bpy_type = Scene
    bpy_idname = "bf_misc_fyi"


@subscribe
class SP_MISC_OVERWRITE(BFParam):
    label = "OVERWRITE"
    description = "Do not check for the existence of CHID.out and overwrite files"
    fds_label = "OVERWRITE"
    fds_default = True
    bpy_type = Scene
    bpy_prop = BoolProperty
    bpy_idname = "bf_misc_overwrite"


@subscribe
class SP_MISC_THICKEN_OBSTRUCTIONS(BFParam):
    label = "THICKEN_OBSTRUCTIONS"
    description = "Do not allow thin sheet obstructions"
    fds_label = "THICKEN_OBSTRUCTIONS"
    fds_default = False
    bpy_type = Scene
    bpy_prop = BoolProperty
    bpy_idname = "bf_misc_thicken_obstructions"


@subscribe
class SP_MISC_other(BFParamOther):
    bpy_type = Scene
    bpy_idname = "bf_misc_other"
    bpy_pg = WM_PG_bf_other
    bpy_ul = WM_UL_bf_other_items


@subscribe
class SN_MISC(BFNamelistSc):
    label = "MISC"
    description = "Miscellaneous parameters"
    enum_id = 3003
    fds_label = "MISC"
    bpy_export = "bf_misc_export"
    bpy_export_default = False
    bf_params = (
        SP_MISC_FYI,
        SP_MISC_OVERWRITE,
        SP_MISC_THICKEN_OBSTRUCTIONS,
        SP_MISC_other,
    )
    maxlen = 0

# FIXME MOVE namelist

# REAC

@subscribe
class SP_REAC_ID(BFParamStr):
    label = "ID"
    description = "Identificator of the reaction"
    fds_label = "ID"
    bpy_type = Scene
    bpy_idname = "bf_reac_id"

@subscribe
class SP_REAC_FYI(BFParamFYI):
    bpy_type = Scene
    bpy_idname = "bf_reac_fyi"

@subscribe
class SP_REAC_FUEL(BFParamStr):  # FIXME from table
    label = "FUEL"
    description = "Identificator of fuel species"
    fds_label = "FUEL"
    bpy_type = Scene
    bpy_idname = "bf_reac_fuel"


@subscribe
class SP_REAC_FORMULA(BFParamStr):
    label = "FORMULA"
    description = "Chemical formula of fuel species, it can only contain C, H, O, or N"
    fds_label = "FORMULA"
    bpy_type = Scene
    bpy_idname = "bf_reac_formula"


@subscribe
class SP_REAC_CO_YIELD(BFParam):
    label = "CO_YIELD [kg/kg]"
    description = "Fraction of fuel mass converted into carbon monoxide"
    fds_label = "CO_YIELD"
    fds_default = 0.0
    bpy_type = Scene
    bpy_prop = FloatProperty
    bpy_idname = "bf_reac_co_yield"
    bpy_other = {"step": 1.0, "precision": 3, "min": 0.0, "max": 1.0}


@subscribe
class SP_REAC_SOOT_YIELD(SP_REAC_CO_YIELD):
    label = "SOOT_YIELD [kg/kg]"
    description = "Fraction of fuel mass converted into smoke particulate"
    fds_label = "SOOT_YIELD"
    bpy_type = Scene
    bpy_idname = "bf_reac_soot_yield"


@subscribe
class SP_REAC_HEAT_OF_COMBUSTION(BFParam):
    label = "HEAT_OF_COMBUSTION [kJ/kg]"
    description = "Fuel heat of combustion"
    fds_label = "HEAT_OF_COMBUSTION"
    fds_default = 0.0
    bpy_type = Scene
    bpy_idname = "bf_reac_heat_of_combustion"
    bpy_prop = FloatProperty
    bpy_other = {"precision": 1, "min": 0.0}


@subscribe
class SP_REAC_IDEAL(BFParam):
    label = "IDEAL"
    description = "Set ideal heat of combustion"
    fds_label = "IDEAL"
    fds_default = False
    bpy_type = Scene
    bpy_prop = BoolProperty
    bpy_idname = "bf_reac_ideal"

@subscribe
class SP_REAC_RADIATIVE_FRACTION(BFParam):
    label = "RADIATIVE_FRACTION"
    description = (
        "Fraction of the total combustion energy that is released "
        "in the form of thermal radiation"
    )
    fds_label = "RADIATIVE_FRACTION"
    fds_default = 0.35
    bpy_type = Scene
    bpy_idname = "bf_reac_radiative_fraction"
    bpy_prop = FloatProperty
    bpy_other = {"precision": 2, "min": 0.0, "max": 1.0}

@subscribe
class SP_REAC_other(BFParamOther):
    bpy_type = Scene
    bpy_idname = "bf_reac_other"
    bpy_pg = WM_PG_bf_other
    bpy_ul = WM_UL_bf_other_items


@subscribe
class SN_REAC(BFNamelistSc):
    label = "REAC"
    description = "Reaction"
    enum_id = 3004
    fds_label = "REAC"
    bpy_export = "bf_reac_export"
    bf_params = (
        SP_REAC_ID,
        SP_REAC_FUEL,
        SP_REAC_FYI,
        SP_REAC_FORMULA,
        SP_REAC_CO_YIELD,
        SP_REAC_SOOT_YIELD,
        SP_REAC_HEAT_OF_COMBUSTION,
        SP_REAC_IDEAL,
        SP_REAC_RADIATIVE_FRACTION,  # moved from RADI
        SP_REAC_other,
    )
    maxlen = 0


# RADI


@subscribe
class SP_RADI_FYI(BFParamFYI):
    bpy_type = Scene
    bpy_idname = "bf_radi_fyi"


@subscribe
class SP_RADI_RADIATION(BFParam):
    label = "RADIATION"
    description = "Turn on/off the radiation solver"
    fds_label = "RADIATION"
    fds_default = True
    bpy_type = Scene
    bpy_prop = BoolProperty
    bpy_idname = "bf_radi_radiation"


@subscribe
class SP_RADI_NUMBER_RADIATION_ANGLES(BFParam):
    label = "NUMBER_RADIATION_ANGLES"
    description = "Number of angles for spatial resolution of radiation solver"
    fds_label = "NUMBER_RADIATION_ANGLES"
    fds_default = 100
    bpy_type = Scene
    bpy_idname = "bf_radi_number_radiation_angles"
    bpy_prop = IntProperty
    bpy_other = {"min": 1}


@subscribe
class SP_RADI_TIME_STEP_INCREMENT(BFParam):
    label = "TIME_STEP_INCREMENT"
    description = "Frequency of calls to the radiation solver in time steps"
    fds_label = "TIME_STEP_INCREMENT"
    fds_default = 3
    bpy_type = Scene
    bpy_idname = "bf_radi_time_step_increment"
    bpy_prop = IntProperty
    bpy_other = {"min": 1}


@subscribe
class SP_RADI_ANGLE_INCREMENT(BFParam):
    label = "ANGLE_INCREMENT"
    description = "Increment over which the angles are updated"
    fds_label = "ANGLE_INCREMENT"
    fds_default = 5
    bpy_type = Scene
    bpy_idname = "bf_radi_angle_increment"
    bpy_prop = IntProperty
    bpy_other = {"min": 1}


@subscribe
class SP_RADI_RADIATION_ITERATIONS(BFParam):
    label = "RADIATION_ITERATIONS"
    description = "Number of times the radiative intensity is updated in a time step"
    fds_label = "RADIATION_ITERATIONS"
    fds_default = 1
    bpy_type = Scene
    bpy_idname = "bf_radi_radiation_iterations"
    bpy_prop = IntProperty
    bpy_other = {"min": 1}


@subscribe
class SP_RADI_other(BFParamOther):
    bpy_type = Scene
    bpy_idname = "bf_radi_other"
    bpy_pg = WM_PG_bf_other
    bpy_ul = WM_UL_bf_other_items


@subscribe
class SN_RADI(BFNamelistSc):
    label = "RADI"
    description = "Radiation parameters"
    enum_id = 3006
    fds_label = "RADI"
    bpy_export = "bf_radi_export"
    bpy_export_default = False
    bf_params = (
        SP_RADI_FYI,
        SP_RADI_RADIATION,
        SP_RADI_NUMBER_RADIATION_ANGLES,
        SP_RADI_TIME_STEP_INCREMENT,
        SP_RADI_ANGLE_INCREMENT,
        SP_RADI_RADIATION_ITERATIONS,
        SP_RADI_other,
    )
    maxlen = 0


# DUMP


@subscribe
class SP_DUMP_FYI(BFParamFYI):
    bpy_type = Scene
    bpy_idname = "bf_dump_fyi"


@subscribe
class SP_DUMP_render_file(BFParam):
    label = "Export Geometric Description File"
    description = "Export geometric description file GE1"
    fds_label = "RENDER_FILE"
    bpy_type = Scene
    bpy_idname = "bf_dump_render_file"
    bpy_prop = BoolProperty
    bpy_default = False

    @property
    def value(self):
        if self.element.bf_dump_render_file:
            return f"{self.element.name}.ge1"

    def set_value(self, context, value):  # in FDS it is a str!
        self.element.bf_dump_render_file = bool(value)         

@subscribe
class SP_DUMP_STATUS_FILES(BFParam):
    label = "STATUS_FILES"
    description = "Export status file (*.notready), deleted when the simulation is completed successfully"
    fds_label = "STATUS_FILES"
    fds_default = False
    bpy_type = Scene
    bpy_prop = BoolProperty
    bpy_idname = "bf_dump_status_files"


@subscribe
class SP_DUMP_NFRAMES(BFParam):
    label = "NFRAMES"
    description = "Number of output dumps per calculation"
    fds_label = "NFRAMES"
    fds_default = 1000
    bpy_type = Scene
    bpy_idname = "bf_dump_nframes"
    bpy_prop = IntProperty
    bpy_other = {"min": 1}


@subscribe
class SP_DUMP_set_frequency(BFParam):
    label = "Dump Output every 1 s"
    description = "Dump output every 1 s"
    bpy_type = Scene
    bpy_idname = "bf_dump_set_frequency"
    bpy_prop = BoolProperty
    bpy_default = False


@subscribe
class SP_DUMP_DT_RESTART(BFParam):
    label = "DT_RESTART [s]"
    description = "Time interval between restart files are saved"
    fds_label = "DT_RESTART"
    fds_default = 600
    bpy_type = Scene
    bpy_idname = "bf_dump_dt_restart"
    bpy_prop = IntProperty
    bpy_other = {"min": 1}


@subscribe
class SP_DUMP_other(BFParamOther):
    bpy_type = Scene
    bpy_idname = "bf_dump_other"
    bpy_pg = WM_PG_bf_other
    bpy_ul = WM_UL_bf_other_items


@subscribe
class SN_DUMP(BFNamelistSc):
    label = "DUMP"
    description = "Output parameters"
    enum_id = 3005
    fds_label = "DUMP"
    bpy_export = "bf_dump_export"
    bpy_export_default = False
    bf_params = (
        SP_DUMP_FYI,
        SP_DUMP_render_file,
        SP_DUMP_STATUS_FILES,
        SP_DUMP_NFRAMES,
        SP_DUMP_set_frequency,
        SP_DUMP_DT_RESTART,
        SP_DUMP_other,
    )
    maxlen = 0


# CATF


@subscribe
class SP_CATF_check_files(BFParam):
    label = "Check File Existance While Exporting"
    description = "Check file existence while exporting filepaths"
    bpy_type = Scene
    bpy_idname = "bf_catf_check_files"
    bpy_prop = BoolProperty
    bpy_default = False

    def to_fds_param(self, context):
        return


@subscribe
class SP_CATF_files(BFParamOther):
    label = "Concatenated File Paths"
    description = "Concatenated files (eg. PROP='/drive/test.catf')"
    fds_label = "OTHER_FILES"
    bpy_type = Scene
    bpy_idname = "bf_catf_files"
    bpy_pg = WM_PG_bf_filepaths
    bpy_ul = WM_UL_bf_filepaths_items

    def to_fds_param(self, context):
        el = self.element
        coll = getattr(self.element, self.bpy_idname)
        result = list()
        for p in coll:
            if p.bf_export and p.name:
                if el.bf_catf_check_files and not utils.is_file(p.name):
                    raise BFException(self, f"File path <{p.name}> does not exist")
                result.append(
                    tuple((FDSParam(label=f"OTHER_FILES='{p.name}'"),))
                )  # multi param
        return tuple(result)  # multi

    def from_fds(self, context, value):
        if not value:
            self.set_value(context, None)
        elif isinstance(value, str):  # str
            self.set_value(context, value)
        else:  # tuple of str
            for v in value:
                self.set_value(context, v)

@subscribe
class SN_CATF(BFNamelistSc):
    label = "CATF"
    description = "Concatenated file paths"
    fds_label = "CATF"
    bpy_export = "bf_catf_export"
    bpy_export_default = False
    bf_params = SP_CATF_check_files, SP_CATF_files
    maxlen = 0


# Material


def update_MP_namelist_cls(self, context):
    # Set default appearance
    self.set_default_appearance(context)


@subscribe
class MP_namelist_cls(BFParam):
    label = "Namelist"
    description = "Identification of FDS namelist"
    bpy_type = Material
    bpy_idname = "bf_namelist_cls"
    bpy_prop = EnumProperty
    bpy_other = {
        "items": (("MN_SURF", "SURF", "Generic boundary condition", 2000),),
        "update": update_MP_namelist_cls,
    }
    bpy_default = "MN_SURF"

    def to_fds_param(self, context):
        if self.element.name in {"INERT", "HVAC", "MIRROR", "OPEN", "PERIODIC"}:
            return
        super().to_fds_param(context)


@subscribe
class MP_ID(BFParamStr):
    label = "ID"
    description = "Material identification name"
    fds_label = "ID"
    bf_other = {"copy_protect": True}
    bpy_type = Material
    bpy_prop = None  # to avoid creation
    bpy_idname = "name"


@subscribe
class MP_FYI(BFParamFYI):
    bpy_type = Material
    bpy_idname = "bf_fyi"


@subscribe
class MP_RGB(BFParam):  # exports both RGB and TRANSPARENCY
    label = "RGB"
    description = "Color values (red, green, blue)"
    fds_label = "RGB"
    bpy_type = Material
    bpy_prop = None  # Do not register
    bpy_idname = "diffuse_color"

    def set_value(self, context, value):
        c = self.element.diffuse_color
        c[0], c[1], c[2] = value[0] / 255.0, value[1] / 255.0, value[2] / 255.0

    def to_fds_param(self, context):
        c = self.element.diffuse_color
        rs = (int(c[0] * 255), int(c[1] * 255), int(c[2] * 255))
        ts = (c[3],)
        return tuple(
            (
                FDSParam(label="RGB", values=rs),
                FDSParam(label="TRANSPARENCY", values=ts, precision=2),
            )
        )


@subscribe
class MP_COLOR(BFParam):  # for importing only
    label = "COLOR"
    description = "Color"
    fds_label = "COLOR"
    bpy_type = Material
    bpy_prop = None  # Do not register

    def set_value(self, context, value):
        c = self.element.diffuse_color
        rgb = utils.fds_colors.get(value, None)
        if not rgb:
            raise BFException(self, f"Error while setting color <{value}>")
        c[0], c[1], c[2] = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0

    def to_fds_param(self, context):
        pass


@subscribe
class MP_TRANSPARENCY(BFParam):  # for importing only, exported by MP_RGB
    label = "TRANSPARENCY"
    description = "Color values (red, green, blue) and transparency"
    fds_label = "TRANSPARENCY"
    bpy_type = Material
    bpy_prop = None  # Do not register

    def set_value(self, context, value):
        c = self.element.diffuse_color
        c[3] = value

    def to_fds_param(self, context):
        pass


@subscribe
class MP_THICKNESS(BFParam):
    label = "THICKNESS [m]"
    description = "Surface thickness for heat transfer calculation"
    fds_label = "THICKNESS"
    fds_default = 0.0
    bpy_type = Material
    bpy_idname = "bf_thickness"
    bpy_prop = FloatProperty
    bpy_other = {"step": 1.0, "precision": 6, "min": 0.000001}
    bpy_export = "bf_thickness_export"
    bpy_export_default = False


@subscribe
class MP_HRRPUA(BFParam):
    label = "HRRPUA [kW/m²]"
    description = "Heat release rate per unit area"
    fds_label = "HRRPUA"
    fds_default = 0.0
    bpy_type = Material
    bpy_idname = "bf_hrrpua"
    bpy_prop = FloatProperty
    bpy_other = {"precision": 3, "min": 0.0}


@subscribe
class MP_TAU_Q(BFParam):
    label = "TAU_Q [s]"
    description = "Ramp time for heat release rate"
    fds_label = "TAU_Q"
    fds_default = 1.0
    bpy_type = Material
    bpy_idname = "bf_tau_q"
    bpy_prop = FloatProperty
    bpy_other = {"step": 10.0, "precision": 1}


@subscribe
class MP_MATL_ID(BFParamStr):
    label = "MATL_ID"
    description = "Reference to a MATL (Material) line for self properties"
    fds_label = "MATL_ID"
    bpy_type = Material
    bpy_idname = "bf_matl_id"
    bpy_export = "bf_matl_id_export"
    bpy_export_default = False

    def draw_operators(self, context, layout):
        layout.operator("material.bf_choose_matl_id", icon="VIEWZOOM", text="")


@subscribe
class MP_IGNITION_TEMPERATURE(BFParam):
    label = "IGNITION_TEMPERATURE [°C]"
    description = "Ignition temperature"
    fds_label = "IGNITION_TEMPERATURE"
    fds_default = 5000.0
    bpy_type = Material
    bpy_idname = "bf_ignition_temperature"
    bpy_prop = FloatProperty
    bpy_other = {"step": 100.0, "precision": 1, "min": -273.0}
    bpy_export = "bf_ignition_temperature_export"
    bpy_export_default = False


@subscribe
class MP_BACKING(BFParam):
    label = "BACKING"
    description = "Exposition of back side surface"
    fds_label = "BACKING"
    fds_default = "EXPOSED"
    bpy_type = Material
    bpy_idname = "bf_backing"
    bpy_prop = EnumProperty
    bpy_prop_export = "bf_backing_export"
    bpy_export_default = False
    bpy_other = {
        "items": (
            (
                "VOID",
                "VOID",
                "The wall is assumed to back up to the ambient temperature",
            ),
            (
                "INSULATED",
                "INSULATED",
                "The back side of the material is perfectly insulated",
            ),
            (
                "EXPOSED",
                "EXPOSED",
                "The heat transfer into the space behind the wall is calculated (only if wall is one cell thick)",
            ),
        )
    }


@subscribe
class MP_other(BFParamOther):
    bpy_type = Material
    bpy_idname = "bf_other"
    bpy_pg = WM_PG_bf_other
    bpy_ul = WM_UL_bf_other_items


@subscribe
class MN_SURF(BFNamelistMa):
    label = "SURF"
    description = "Generic boundary condition"
    enum_id = 2000
    fds_label = "SURF"
    bpy_export = "bf_surf_export"
    bpy_export_default = True
    bf_params = (
        MP_ID,
        MP_FYI,
        MP_RGB,
        MP_COLOR,
        MP_TRANSPARENCY,
        MP_MATL_ID,
        MP_THICKNESS,
        MP_BACKING,
        MP_HRRPUA,
        MP_TAU_Q,
        MP_IGNITION_TEMPERATURE,
        MP_other,
    )
    maxlen = 0

    @property
    def exported(self) -> "bool":
        return self.element.bf_surf_export and self.element.name not in default_mas


# Object


def update_OP_namelist_cls(ob, context):
    # Remove tmp Objects
    geometry.utils.rm_tmp_objects(context)
    # Set default appearance
    ob.set_default_appearance(context)


@subscribe
class OP_namelist_cls(BFParam):
    label = "Namelist"
    description = "Identification of FDS namelist"
    bpy_type = Object
    bpy_idname = "bf_namelist_cls"
    bpy_prop = EnumProperty
    bpy_default = "ON_OBST"
    bpy_other = {
        "items": (("ON_OBST", "OBST", "Obstruction", 1000),),
        "update": update_OP_namelist_cls,
    }


# OBST


@subscribe
class OP_ID(BFParamStr):
    label = "ID"
    description = "Object identification name"
    fds_label = "ID"
    bf_other = {"copy_protect": True}
    bpy_type = Object
    bpy_prop = None  # to avoid creation
    bpy_idname = "name"


@subscribe
class OP_FYI(BFParamFYI):
    bpy_type = Object
    bpy_idname = "bf_fyi"


def update_bf_xb(ob, context):
    geometry.utils.rm_tmp_objects(context)
    if ob.bf_xb in ("VOXELS", "FACES", "PIXELS", "EDGES") and ob.bf_xb_export:
        if ob.bf_xyz == "VERTICES":
            ob.bf_xyz_export = False
        if ob.bf_pb == "PLANES":
            ob.bf_pb_export = False
        return


@subscribe
class OP_XB_custom_voxel(BFParam):
    label = "Use Custom Voxel/Pixel"
    description = "Use custom voxel/pixel size for current Object"
    bpy_type = Object
    bpy_idname = "bf_xb_custom_voxel"
    bpy_prop = BoolProperty
    bpy_default = False
    bpy_other = {"update": update_bf_xb}


@subscribe
class OP_XB_voxel_size(BFParam):
    label = "Custom Voxel/Pixel Size"
    description = "Custom voxel/pixel size for current Object"
    bpy_type = Object
    bpy_idname = "bf_xb_voxel_size"
    bpy_prop = FloatProperty
    bpy_default = 0.1
    bpy_other = {
        "step": 1.0,
        "precision": 3,
        "min": 0.001,
        "max": 20.0,
        "unit": "LENGTH",
        "update": update_bf_xb,
    }
    bpy_export = "bf_xb_custom_voxel"


@subscribe
class OP_XB_center_voxels(BFParam):
    label = "Center Voxels/Pixels"
    description = "Center voxels/pixels to Object bounding box"
    bpy_type = Object
    bpy_idname = "bf_xb_center_voxels"
    bpy_prop = BoolProperty
    bpy_default = False
    bpy_other = {"update": update_bf_xb}


@subscribe
class OP_XB_export(BFParam):
    label = "Export XB"
    description = "Set if XB shall be exported to FDS"
    bpy_type = Object
    bpy_idname = "bf_xb_export"
    bpy_prop = BoolProperty
    bpy_default = True
    bpy_other = {"update": update_bf_xb}


@subscribe
class OP_XB(BFParamXB):
    label = "XB"
    description = "Export as volumes/faces"
    fds_label = "XB"
    bpy_type = Object
    bpy_idname = "bf_xb"
    bpy_prop = EnumProperty
    bpy_other = {
        "update": update_bf_xb,
        "items": (
            ("BBOX", "BBox", "Export object bounding box"),
            ("VOXELS", "Voxels", "Export voxels from voxelized solid Object"),
            ("FACES", "Faces", "Export faces, one for each face"),
            ("PIXELS", "Pixels", "Export pixels from pixelized flat Object"),
            ("EDGES", "Edges", "Export segments, one for each edge"),
        ),
    }
    bpy_export = "bf_xb_export"
    bf_xb_from_fds = None  # auto

    def draw(self, context, layout):
        super().draw(context, layout)
        ob = self.element
        if ob.bf_xb_export and ob.bf_xb in ("VOXELS", "PIXELS"):
            OP_XB_center_voxels(ob).draw(context, layout)
            OP_XB_voxel_size(ob).draw(context, layout)

    def to_fds_param(self, context):
        ob = self.element
        if not ob.bf_xb_export:
            return
        # Compute
        scale_length = context.scene.unit_settings.scale_length
        xbs, msg = geometry.to_fds.ob_to_xbs(context, ob, scale_length)
        # Single param
        if len(xbs) == 1:
            return FDSParam(label="XB", values=xbs[0], precision=6)
        # Multi param, prepare new ID
        n = ob.name
        suffix = self.element.bf_id_suffix
        if suffix == "IDI":
            ids = (f"{n}_{i}" for i, _ in enumerate(xbs))
        elif suffix == "IDX":
            ids = (f"{n}_x{xb[0]:+.3f}" for xb in xbs)
        elif suffix == "IDY":
            ids = (f"{n}_y{xb[2]:+.3f}" for xb in xbs)
        elif suffix == "IDZ":
            ids = (f"{n}_z{xb[4]:+.3f}" for xb in xbs)
        elif suffix == "IDXY":
            ids = (f"{n}_x{xb[0]:+.3f}_y{xb[2]:+.3f}" for xb in xbs)
        elif suffix == "IDXZ":
            ids = (f"{n}_x{xb[0]:+.3f}_z{xb[4]:+.3f}" for xb in xbs)
        elif suffix == "IDYZ":
            ids = (f"{n}_y{xb[2]:+.3f}_z{xb[4]:+.3f}" for xb in xbs)
        elif suffix == "IDXYZ":
            ids = (f"{n}_x{xb[0]:+.3f}_y{xb[2]:+.3f}_z{xb[4]:+.3f}" for xb in xbs)
        else:
            raise Exception("Unknown suffix <{suffix}>")
        result = tuple(
            (
                FDSParam(label="ID", values=(hid,)),
                FDSParam(label="XB", values=xb, precision=6),
            )
            for hid, xb in zip(ids, xbs)
        )  # multi
        # Send message
        result[0][0].msg = msg
        return result

    def from_fds(self, context, value):
        scale_length = context.scene.unit_settings.scale_length
        try:
            bf_xb = geometry.from_fds.xbs_to_ob(
                xbs=(value,),
                context=context,
                ob=self.element,
                scale_length=scale_length,
                bf_xb=self.bf_xb_from_fds,  # auto or forced
            )
        except Exception as err:
            raise BFException(self, f"Error importing <{value}> value, {str(err)}")
        else:
            self.element.bf_xb = bf_xb
            self.element.bf_xb_export = True
            self.set_exported(context, True)


def update_bf_xyz(ob, context):
    geometry.utils.rm_tmp_objects(context)
    if ob.bf_xyz == "VERTICES" and ob.bf_xyz_export:
        if ob.bf_xb in ("VOXELS", "FACES", "PIXELS", "EDGES"):
            ob.bf_xb_export = False
        if ob.bf_pb == "PLANES":
            ob.bf_pb_export = False
        return


@subscribe
class OP_XYZ_export(BFParam):
    label = "Export XYZ"
    description = "Set if XYZ shall be exported to FDS"
    bpy_type = Object
    bpy_idname = "bf_xyz_export"
    bpy_prop = BoolProperty
    bpy_default = False
    bpy_other = {"update": update_bf_xyz}


@subscribe
class OP_XYZ(BFParamXYZ):
    label = "XYZ"
    description = "Export as points"
    fds_label = "XYZ"
    bpy_type = Object
    bpy_idname = "bf_xyz"
    bpy_prop = EnumProperty
    bpy_other = {
        "update": update_bf_xyz,
        "items": (
            ("CENTER", "Center", "Point, center point of this object"),
            ("VERTICES", "Vertices", "Points, one for each vertex of this object"),
        ),
    }
    bpy_export = "bf_xyz_export"

    def to_fds_param(self, context):
        ob = self.element
        if not ob.bf_xyz_export:
            return
        # Compute
        scale_length = context.scene.unit_settings.scale_length
        xyzs, msg = geometry.to_fds.ob_to_xyzs(context, ob, scale_length)
        # Single param
        if len(xyzs) == 1:
            return FDSParam(label="XYZ", values=xyzs[0])
        # Multi param, prepare new ID
        n = ob.name
        suffix = self.element.bf_id_suffix
        if suffix == "IDI":
            ids = (f"{n}_{i}" for i, _ in enumerate(xyzs))
        elif suffix == "IDX":
            ids = (f"{n}_x{xyz[0]:+.3f}" for xyz in xyzs)
        elif suffix == "IDY":
            ids = (f"{n}_y{xyz[1]:+.3f}" for xyz in xyzs)
        elif suffix == "IDZ":
            ids = (f"'{n}_z{xyz[2]:+.3f}" for xyz in xyzs)
        elif suffix == "IDXY":
            ids = (f"{n}_x{xyz[0]:+.3f}_y{xyz[1]:+.3f}" for xyz in xyzs)
        elif suffix == "IDXZ":
            ids = (f"{n}_x{xyz[0]:+.3f}_z{xyz[2]:+.3f}" for xyz in xyzs)
        elif suffix == "IDYZ":
            ids = (f"{n}_y{xyz[1]:+.3f}_z{xyz[2]:+.3f}" for xyz in xyzs)
        elif suffix == "IDXYZ":
            ids = (f"{n}_x{xyz[0]:+.3f}_y{xyz[1]:+.3f}_z{xyz[2]:+.3f}" for xyz in xyzs)
        else:
            raise Exception("Unknown suffix <{suffix}>")
        result = tuple(
            (
                FDSParam(label="ID", values=(hid,)),
                FDSParam(label="XYZ", values=xyz, precision=6),
            )
            for hid, xyz in zip(ids, xyzs)
        )  # multi
        # Send message
        result[0][0].msg = msg
        return result

    def from_fds(self, context, value):
        scale_length = context.scene.unit_settings.scale_length
        try:
            bf_xyz = geometry.from_fds.xyzs_to_ob(
                xyzs=(value,),
                context=context,
                ob=self.element,
                scale_length=scale_length,
            )
        except Exception as err:
            raise BFException(self, f"Error importing <{value}> value, {str(err)}")
        else:
            self.element.bf_xyz = bf_xyz
            self.element.bf_xyz_export = True
            self.set_exported(context, True)


@subscribe
class OP_XYZ_center(OP_XYZ):
    description = "Export as points (center)"
    bpy_prop = None  # do not redefine
    bf_xyz_idxs = (0,)  # CENTER, VERTICES


def update_bf_pb(ob, context):
    geometry.utils.rm_tmp_objects(context)
    if ob.bf_pb == "PLANES" and ob.bf_pb_export:
        if ob.bf_xb in ("VOXELS", "FACES", "PIXELS", "EDGES"):
            ob.bf_xb_export = False
        if ob.bf_xyz == "VERTICES":
            ob.bf_xyz_export = False
        return


@subscribe
class OP_PB_export(BFParam):
    label = "Export PBX, PBY, PBZ"
    description = "Set if PBX, PBY, PBZ shall be exported to FDS"
    bpy_type = Object
    bpy_idname = "bf_pb_export"
    bpy_prop = BoolProperty
    bpy_default = False
    bpy_other = {"update": update_bf_pb}


@subscribe
class OP_PB(BFParamPB):
    label = "PBX, PBY, PBZ"
    description = "Export as planes"
    bpy_type = Object
    bpy_idname = "bf_pb"
    bpy_prop = EnumProperty
    bpy_other = {
        "update": update_bf_pb,
        "items": (("PLANES", "Planes", "Planes, one for each face of this object"),),
    }
    bpy_export = "bf_pb_export"

    axis = None  # axis for importing

    def to_fds_param(self, context):
        ob = self.element
        if not ob.bf_pb_export:
            return
        # Compute
        # pbs is: (0, 3.5), (0, 4.), (2, .5) ...
        # with 0, 1, 2 perpendicular axis
        scale_length = context.scene.unit_settings.scale_length
        pbs, msg = geometry.to_fds.ob_to_pbs(context, ob, scale_length)
        # Prepare labels
        labels = tuple(f"PB{('X','Y','Z')[axis]}" for axis, _ in pbs)
        # Single param
        if len(pbs) == 1:
            return FDSParam(label=labels[0], values=(pbs[0][1],), precision=6)
        # Multi param, prepare new ID
        n = ob.name
        suffix = self.element.bf_id_suffix
        if suffix == "IDI":
            ids = (f"{n}_{i}" for i, _ in enumerate(pbs))
        elif suffix == "IDXYZ":
            ids = (
                (f"{n}_x{pb:+.3f}", f"{n}_y{pb:+.3f}", f"{n}_z{pb:+.3f}")[axis]
                for axis, pb in pbs
            )
        else:
            raise Exception("Unknown suffix <{suffix}>")
        result = tuple(
            (
                FDSParam(label="ID", values=(hid,)),
                FDSParam(label=label, values=(pb,), precision=6),
            )
            for hid, label, (_, pb) in zip(ids, labels, pbs)
        )  # multi
        # Send message
        result[0][0].msg = msg
        return result

    def from_fds(self, context, value):
        scale_length = context.scene.unit_settings.scale_length
        try:
            bf_pb = geometry.from_fds.pbs_to_ob(
                pbs=((self.axis, value[0]),),
                context=context,
                ob=self.element,
                scale_length=scale_length,
            )
        except Exception as err:
            raise BFException(self, f"Error importing <{value}> value, {str(err)}")
        else:
            self.element.bf_pb = bf_pb
            self.element.bf_pb_export = True
            self.set_exported(context, True)


@subscribe
class OP_PBX(OP_PB):  # for importing only
    fds_label = "PBX"
    bpy_prop = None  # already defined
    axis = 0  # axis for importing

    def draw(self, context, layout):
        return

    def to_fds_param(self, context):
        return


@subscribe
class OP_PBY(OP_PBX):  # for importing only
    fds_label = "PBY"
    axis = 1  # axis for importing


@subscribe
class OP_PBZ(OP_PBX):  # for importing only
    fds_label = "PBZ"
    axis = 2  # axis for importing


@subscribe
class OP_ID_suffix(BFParam):
    label = "IDs Suffix"
    description = "Append suffix to multiple ID values"
    bpy_type = Object
    bpy_idname = "bf_id_suffix"
    bpy_prop = EnumProperty
    bpy_other = {
        "items": (
            ("IDI", "Index", "Append index number to multiple ID values"),
            ("IDX", "x", "Append x coordinate to multiple ID values"),
            ("IDY", "y", "Append y coordinate to multiple ID values"),
            ("IDZ", "z", "Append z coordinate to multiple ID values"),
            ("IDXY", "xy", "Append x,y coordinates to multiple ID values"),
            ("IDXZ", "xz", "Append x,z coordinates to multiple ID values"),
            ("IDYZ", "yz", "Append y,z coordinates to multiple ID values"),
            ("IDXYZ", "xyz", "Append x,y,z coordinates to multiple ID values"),
        )
    }

    def draw(self, context, layout):
        ob = self.element
        if (
            (ob.bf_xb_export and ob.bf_xb in ("VOXELS", "PIXELS"))
            or (ob.bf_xyz_export and ob.bf_xyz == "VERTICES")
            or ob.bf_pb_export
        ):
            layout.prop(ob, "bf_id_suffix")
        return layout


@subscribe
class OP_SURF_ID(BFParam):
    label = "SURF_ID"
    description = "Reference to SURF"
    fds_label = "SURF_ID"
    bpy_type = Object
    bpy_idname = "active_material"
    bpy_export = "bf_surf_id_export"
    bpy_export_default = True

    @property
    def value(self):
        if self.element.active_material:
            return self.element.active_material.name

    def set_value(self, context, value):
        if value is None:
            self.element.active_material = None
        else:
            try:
                ma = bpy.data.materials.get(value)
            except IndexError:
                raise BFException(self, f"Blender Material <{value}> does not exists")
            else:
                self.element.active_material = ma

    @property
    def exported(self):
        ob = self.element
        return ob.bf_surf_id_export and ob.active_material


@subscribe
class OP_other(BFParamOther):
    bpy_type = Object
    bpy_idname = "bf_other"
    bpy_pg = WM_PG_bf_other
    bpy_ul = WM_UL_bf_other_items


@subscribe
class ON_OBST(BFNamelistOb):
    label = "OBST"
    description = "Obstruction"
    enum_id = 1000
    fds_label = "OBST"
    bf_params = OP_ID, OP_FYI, OP_SURF_ID, OP_XB, OP_ID_suffix, OP_other
    bf_other = {"appearance": "TEXTURED"}


# Other


@subscribe
class OP_other_namelist(BFParam):
    label = "Label"
    description = "Other namelist label, eg <ABCD>"
    bpy_type = Object
    bpy_prop = StringProperty
    bpy_idname = "bf_other_namelist"
    bpy_default = "ABCD"
    bpy_other = {"maxlen": 4}

    def check(self, context):
        if not re.match("^[A-Z0-9_]{4}$", self.element.bf_other_namelist):
            raise BFException(self, "Malformed other namelist label")


@subscribe
class ON_other(BFNamelistOb):
    label = "Other"
    description = "Other namelist"
    enum_id = 1007
    bf_params = (
        OP_other_namelist,
        OP_ID,
        OP_FYI,
        OP_SURF_ID,
        OP_XB,
        OP_XYZ,
        OP_PB,
        OP_PBX,
        OP_PBY,
        OP_PBZ,
        OP_ID_suffix,
        OP_other,
    )
    bf_other = {"appearance": "TEXTURED"}

    @property
    def fds_label(self):
        return self.element.bf_other_namelist


# GEOM


@subscribe
class OP_GEOM_check_quality(BFParam):
    label = "Check Quality While Exporting"
    description = "Check if closed orientable manifold, with no degenerate geometry while exporting"
    bpy_type = Object
    bpy_idname = "bf_geom_check_quality"
    bpy_prop = BoolProperty
    bpy_default = True


@subscribe
class OP_GEOM_protect(BFParam):
    label = "Protect Original"
    description = "Protect original Object geometry while checking quality"
    bpy_type = Object
    bpy_idname = "bf_geom_protect"
    bpy_prop = BoolProperty
    bpy_default = True


@subscribe
class OP_GEOM(BFParam):
    label = "Geometry Parameters"
    description = "Geometry parameters"
    bpy_type = Object

    def draw(self, context, layout):
        pass

    def to_fds_param(self, context):
        # Check is performed while exporting
        # Get surf_idv, verts and faces
        scale_length = context.scene.unit_settings.scale_length
        check = self.element.bf_geom_check_quality
        fds_surfids, fds_verts, fds_faces, msg = geometry.to_fds.ob_to_geom(
            context, self.element, scale_length, check
        )
        if not fds_faces:
            return None, msg
        # Group by 3 and 4
        verts = [t for t in zip(*[iter(fds_verts)] * 3)]
        faces = [t for t in zip(*[iter(fds_faces)] * 4)]
        # Prepare
        surfids_str = ",".join(("'{}'".format(s) for s in fds_surfids))
        separator1 = "\n      "
        separator2 = "\n            "
        verts_str = separator2.join(
            ("{0[0]:+.6f}, {0[1]:+.6f}, {0[2]:+.6f},".format(v) for v in verts)
        )
        faces_str = separator2.join(
            ("{0[0]},{0[1]},{0[2]}, {0[3]},".format(f) for f in faces)
        )
        return FDSParam(
            label=separator1.join(
                (f"SURF_ID={surfids_str}", f"VERTS={verts_str}", f"FACES={faces_str}")
            ),
            msg=msg,
        )


@subscribe
class OP_GEOM_IS_TERRAIN(BFParam):  # FIXME
    label = "IS_TERRAIN"
    description = "Set if it represents a terrain"
    fds_label = "IS_TERRAIN"
    fds_default = False
    bpy_type = Object
    bpy_prop = BoolProperty
    bpy_idname = "bf_geom_is_terrain"


@subscribe
class OP_GEOM_EXTEND_TERRAIN(BFParam):  # FIXME
    label = "EXTEND_TERRAIN"
    description = "Set if this terrain needs extension to fully cover the domain"
    fds_label = "EXTEND_TERRAIN"
    fds_default = False
    bpy_type = Object
    bpy_prop = BoolProperty
    bpy_idname = "bf_geom_extend_terrain"

    @property
    def exported(self):
        ob = self.element
        return ob.bf_geom_is_terrain


@subscribe
class ON_GEOM(BFNamelistOb):
    label = "GEOM"
    description = "Geometry"
    enum_id = 1021
    fds_label = "GEOM"
    bf_params = (
        OP_ID,
        OP_FYI,
        OP_GEOM_check_quality,
        OP_GEOM_IS_TERRAIN,
        OP_GEOM_EXTEND_TERRAIN,
        OP_GEOM,
        OP_other,
    )
    bf_other = {"appearance": "TEXTURED"}


# HOLE


@subscribe
class ON_HOLE(BFNamelistOb):
    label = "HOLE"
    description = "Obstruction cutout"
    enum_id = 1009
    fds_label = "HOLE"
    bf_params = OP_ID, OP_FYI, OP_XB, OP_other
    bf_other = {"appearance": "DUMMY0"}


# VENT

@subscribe
class OP_VENT_OBST_ID(BFParam):  # FIXME test
    label = "OBST_ID"
    description = "Specify OBST on which projecting the condition"
    fds_label = "OBST_ID"
    bpy_type = Object
    bpy_prop = PointerProperty
    bpy_idname = "bf_vent_obst_id"
    bpy_other = {"type": Object}

    @property
    def value(self):  # FIXME to str
        if self.element.bf_vent_obst_id:
            return self.element.bf_vent_obst_id.name

    def set_value(self, context, value):
        if value:
            ob = bpy.data.objects.get(value)
            if ob:
                self.element.bf_vent_obst_id = ob
            else:
                raise BFException(self, f"Object <{value}> not found")
        else:
            self.element.bf_vent_obst_id = None


@subscribe
class ON_VENT(BFNamelistOb):
    label = "VENT"
    description = "Boundary condition patch"
    enum_id = 1010
    fds_label = "VENT"
    bf_params = (
        OP_ID,
        OP_FYI,
        OP_SURF_ID,
        OP_VENT_OBST_ID,
        OP_XB,
        OP_PB,
        OP_XYZ,
        OP_PBX,
        OP_PBY,
        OP_PBZ,
        OP_ID_suffix,
        OP_other,
    )
    bf_other = {"appearance": "TEXTURED"}


# DEVC


@subscribe
class OP_DEVC_QUANTITY(BFParamStr):
    label = "QUANTITY"
    description = "Output quantity"
    fds_label = "QUANTITY"
    bpy_type = Object
    bpy_idname = "bf_quantity"


@subscribe
class OP_DEVC_SETPOINT(BFParam):
    label = "SETPOINT [~]"
    description = "Value of the device at which its state changes"
    fds_label = "SETPOINT"
    bpy_type = Object
    bpy_idname = "bf_devc_setpoint"
    bpy_prop = FloatProperty
    bpy_default = 0.0
    bpy_other = {"step": 10.0, "precision": 3}
    bpy_export = "bf_devc_setpoint_export"
    bpy_export_default = False


@subscribe
class OP_DEVC_INITIAL_STATE(BFParam):
    label = "INITIAL_STATE"
    description = "Set device initial state"
    fds_label = "INITIAL_STATE"
    fds_default = False
    bpy_type = Object
    bpy_prop = BoolProperty
    bpy_idname = "bf_devc_initial_state"


@subscribe
class OP_DEVC_LATCH(BFParam):
    label = "LATCH"
    description = "Device only changes state once"
    fds_label = "LATCH"
    fds_default = False
    bpy_type = Object
    bpy_prop = BoolProperty
    bpy_idname = "bf_devc_latch"


@subscribe
class OP_DEVC_PROP_ID(BFParamStr):
    label = "PROP_ID"
    description = "Reference to a PROP (Property) line for self properties"
    fds_label = "PROP_ID"
    bpy_type = Object
    bpy_idname = "bf_devc_prop_id"

    def draw_operators(self, context, layout):
        layout.operator("object.bf_choose_devc_prop_id", icon="VIEWZOOM", text="")


@subscribe
class ON_DEVC(BFNamelistOb):
    label = "DEVC"
    description = "Device"
    enum_id = 1011
    fds_label = "DEVC"
    bf_params = (
        OP_ID,
        OP_FYI,
        OP_DEVC_QUANTITY,
        OP_DEVC_SETPOINT,
        OP_DEVC_INITIAL_STATE,
        OP_DEVC_LATCH,
        OP_DEVC_PROP_ID,
        OP_XB,
        OP_XYZ,
        OP_ID_suffix,
        OP_other,
    )
    bf_other = {"appearance": "DUMMY1"}


# SLCF


@subscribe
class OP_SLCF_VECTOR(BFParam):
    label = "VECTOR"
    description = "Create animated vectors"
    fds_label = "VECTOR"
    fds_default = False
    bpy_type = Object
    bpy_prop = BoolProperty
    bpy_idname = "bf_slcf_vector"


@subscribe
class OP_SLCF_CELL_CENTERED(BFParam):
    label = "CELL_CENTERED"
    description = "Output the actual cell-centered data with no averaging"
    fds_label = "CELL_CENTERED"
    fds_default = False
    bpy_type = Object
    bpy_prop = BoolProperty
    bpy_idname = "bf_slcf_cell_centered"


@subscribe
class ON_SLCF(BFNamelistOb):
    label = "SLCF"
    description = "Slice file"
    enum_id = 1012
    fds_label = "SLCF"
    bf_params = (
        OP_ID,
        OP_FYI,
        OP_DEVC_QUANTITY,
        OP_SLCF_VECTOR,
        OP_SLCF_CELL_CENTERED,
        OP_XB,
        OP_PB,
        OP_PBX,
        OP_PBY,
        OP_PBZ,
        OP_ID_suffix,
        OP_other,
    )
    bf_other = {"appearance": "DUMMY1"}


# PROF


@subscribe
class ON_PROF(BFNamelistOb):
    label = "PROF"
    description = "Wall profile output"
    enum_id = 1013
    fds_label = "PROF"
    bf_params = OP_ID, OP_FYI, OP_DEVC_QUANTITY, OP_XYZ, OP_ID_suffix, OP_other
    bf_other = {"appearance": "DUMMY1"}


# MESH


@subscribe
class OP_MESH_IJK(BFParam):
    label = "IJK"
    description = "Cell number in x, y, and z direction"
    fds_label = "IJK"
    bpy_type = Object
    bpy_idname = "bf_mesh_ijk"
    bpy_prop = IntVectorProperty
    bpy_default = (10, 10, 10)
    bpy_other = {"size": 3, "min": 1}
    # bpy_export = "bf_mesh_ijk_export"
    # bpy_export_default = True


@subscribe
class OP_MESH_MPI_PROCESS(BFParam):
    label = "MPI_PROCESS"
    description = "Assigned to given MPI process (Starting from 0.)"
    fds_label = "MPI_PROCESS"
    fds_default = 0
    bpy_type = Object
    bpy_idname = "bf_mesh_mpi_process"
    bpy_prop = IntProperty
    bpy_other = {"min": 0}
    bpy_export = "bf_mesh_mpi_process_export"
    bpy_export_default = False


@subscribe
class ON_MESH(BFNamelistOb):
    label = "MESH"
    description = "Domain of simulation"
    enum_id = 1014
    fds_label = "MESH"
    bf_params = OP_ID, OP_FYI, OP_MESH_IJK, OP_MESH_MPI_PROCESS, OP_XB, OP_other
    bf_other = {"appearance": "WIRE"}


# INIT


@subscribe
class ON_INIT(BFNamelistOb):
    label = "INIT"
    description = "Initial condition"
    enum_id = 1015
    fds_label = "INIT"
    bf_params = OP_ID, OP_FYI, OP_XB, OP_XYZ, OP_ID_suffix, OP_other
    bf_other = {"appearance": "DUMMY2"}


# ZONE


@subscribe
class ON_ZONE(BFNamelistOb):
    label = "ZONE"
    description = "Pressure zone"
    enum_id = 1016
    fds_label = "ZONE"
    bf_params = OP_ID, OP_FYI, OP_XB, OP_XYZ, OP_other
    bf_other = {"appearance": "DUMMY2"}


# HVAC


@subscribe
class ON_HVAC(BFNamelistOb):
    label = "HVAC"
    description = "HVAC system definition"
    enum_id = 1017
    fds_label = "HVAC"
    bf_params = OP_ID, OP_FYI, OP_XYZ, OP_ID_suffix, OP_other
    bf_other = {"appearance": "WIRE"}


# Closing

# Update OP_namelist items

items = [
    (cls.__name__, cls.label, cls.description, cls.enum_id)
    for _, cls in bf_namelists_by_cls.items()
    if cls.bpy_type == Object
]
items.sort(key=lambda k: k[1])
OP_namelist_cls.bpy_other["items"] = items

# Update MP_namelist items

items = [
    (cls.__name__, cls.label, cls.description, cls.enum_id)
    for _, cls in bf_namelists_by_cls.items()
    if cls.bpy_type == Material
]
items.sort(key=lambda k: k[1])
MP_namelist_cls.bpy_other["items"] = items


# Extension of Blender types


class BFObject:
    """Extension of Blender Object."""

    @property
    def bf_namelist(self):
        try:
            return bf_namelists_by_cls[self.bf_namelist_cls](self)
        except IndexError:
            raise BFException(
                self,
                "FDS namelist <{self.bf_namelist_cls}> not supported by Blender Object <{self.name}>",
            )

    def to_fds(self, context):
        if self.bf_is_tmp or not self.type == "MESH":
            return
        return self.bf_namelist.to_fds(context)

    def from_fds(self, context, fds_namelist):
        # Set bf_namelist_cls
        bf_namelist = bf_namelists_by_fds_label.get(fds_namelist.label)
        self.bf_namelist_cls = bf_namelist.__name__
        # Prevent default geometry (eg. XB=BBOX)
        self.bf_xb_export, self.bf_xyz_export, self.bf_pb_export = (False, False, False)
        # Import
        self.bf_namelist.from_fds(context, fds_params=fds_namelist.fds_params)

    def set_default_appearance(self, context):
        # Check preferences and namelist
        prefs = context.preferences.addons[__package__.split(".")[0]].preferences
        if not prefs.bf_pref_appearance:
            return
        bf_namelist = self.bf_namelist
        if not bf_namelist:
            return
        # Init
        appearance = bf_namelist.bf_other.get("appearance")
        ma_inert = bpy.data.materials.get("INERT")
        ma_dummy0 = bpy.data.materials.get("Dummy Color1")  # HOLE
        ma_dummy1 = bpy.data.materials.get("Dummy Color2")  # DEVC, SLCF, PROF, ...
        ma_dummy2 = bpy.data.materials.get("Dummy Color3")  # INIT, ZONE
        # WIRE: MESH, HVAC
        # Set
        if appearance == "TEXTURED" and ma_inert:
            self.active_material = ma_inert
            self.show_wire = False
            self.display_type = "TEXTURED"
            return
        self.show_wire = True
        if appearance == "DUMMY0" and ma_dummy0:
            self.active_material = ma_dummy0
            self.display_type = "SOLID"
        elif appearance == "DUMMY1" and ma_dummy1:
            self.active_material = ma_dummy1
            self.display_type = "SOLID"
        elif appearance == "DUMMY2" and ma_dummy2:
            self.active_material = ma_dummy2
            self.display_type = "SOLID"
        elif appearance == "WIRE":
            self.display_type = "WIRE"

    @classmethod
    def register(cls):
        Object.bf_namelist = cls.bf_namelist
        Object.to_fds = cls.to_fds
        Object.from_fds = cls.from_fds
        Object.set_default_appearance = cls.set_default_appearance

    @classmethod
    def unregister(cls):
        del Object.set_default_appearance
        del Object.from_fds
        del Object.to_fds
        del Object.bf_namelist


class BFMaterial:
    """Extension of Blender Material."""

    @property
    def bf_namelist(self):
        try:
            return bf_namelists_by_cls[self.bf_namelist_cls](self)
        except IndexError:
            raise BFException(
                self,
                "FDS namelist <{self.bf_namelist_cls}> not supported by Blender Material <{self.name}>",
            )

    def to_fds(self, context):
        return self.bf_namelist.to_fds(context)

    def from_fds(self, context, fds_namelist):
        # Set bf_namelist_cls
        self.bf_namelist_cls = "MN_SURF"
        # Import
        self.bf_namelist.from_fds(context, fds_params=fds_namelist.fds_params)

    def set_default_appearance(self, context):  # TODO
        prefs = context.preferences.addons[__package__.split(".")[0]].preferences
        if not prefs.bf_pref_appearance:
            return
        pass

    @classmethod
    def register(cls):
        Material.bf_namelist = cls.bf_namelist
        Material.to_fds = cls.to_fds
        Material.from_fds = cls.from_fds
        Material.set_default_appearance = cls.set_default_appearance

    @classmethod
    def unregister(cls):
        del Material.set_default_appearance
        del Material.from_fds
        del Material.to_fds
        del Material.bf_namelist


class BFScene:
    """Extension of Blender Scene."""

    name = str()  # sc.name
    bf_head_export = bool()  # sc.bf_head_export
    collection = None  # sc.collection

    @property
    def bf_namelists(self):
        return (n(self) for _, n in bf_namelists_by_cls.items() if n.bpy_type == Scene)

    def to_fds(self, context, full=False):
        # Header
        v = sys.modules[__package__].bl_info["version"]
        blv = bpy.app.version_string
        now = time.strftime("%a, %d %b %Y, %H:%M:%S", time.localtime())
        filepath = bpy.data.filepath or "not saved"
        if len(filepath) > 60:
            filepath = "..." + filepath[-57:]
        lines = list(
            (
                f"! Generated by BlenderFDS {v[0]}.{v[1]}.{v[2]} on Blender {blv}",
                f"! File: <{filepath}>",
                f"! Blender Scene: <{self.name}>",
                f"! Date: <{now}>",
            )
        )
        # My namelists
        lines.extend(n.to_fds(context) for n in self.bf_namelists)
        # Free Text
        if self.bf_config_text:
            lines.append(f"\n! --- From <{self.bf_config_text.name}> free text")
            lines.append(self.bf_config_text.as_string())
        # Extend with Materials and Collections
        if full:
            # Materials
            mas = list(bpy.data.materials)
            if mas:
                mas.sort(key=lambda k: k.name)  # alphabetic order by name
                lines.append("\n! --- Boundary conditions from Blender Materials")
                for ma in mas:
                    lines.append(ma.to_fds(context))
            # Objects
            lines.append(context.scene.collection.to_fds(context))
            # Tail
            if self.bf_head_export:
                lines.append("\n&TAIL /")
        return "\n".join(line for line in lines if line)  # remove empties

    def from_fds(self, context, fds_case):
        """Import from FDSCase."""
        self.set_default_appearance(context)  # current scene
        fds_case_un = FDSCase()  # unmanaged namelists
        # Import SURFs first FIXME improve, repetition!
        # FIXME if a material is not available, throw an Exception!
        for fds_namelist in fds_case.fds_namelists:
            if fds_namelist.label != "SURF":
                continue
            hid = f"New {fds_namelist.label}"
            ma = bpy.data.materials.new(hid)
            ma.from_fds(context, fds_namelist=fds_namelist)
            ma.use_fake_user = True # prevent del (eg. used by PART)
            ma.set_default_appearance(context)
        # Then the rest FIXME improve
        for fds_namelist in fds_case.fds_namelists:
            if fds_namelist.label == "SURF":
                continue
            # Get namelist class
            bf_namelist = bf_namelists_by_fds_label.get(fds_namelist.label, None)
            if not bf_namelist:
                # Put unmanaged namelists in fds_case_un
                fds_case_un.fds_namelists.append(fds_namelist)
                continue
            # Prepare default name
            hid = f"New {fds_namelist.label}"
            if bf_namelist.bpy_type == Object:  # new Object
                me = bpy.data.meshes.new(hid)
                ob = bpy.data.objects.new(hid, object_data=me)
                self.collection.objects.link(ob)
                ob.from_fds(context, fds_namelist=fds_namelist)
                ob.set_default_appearance(context)
            elif bf_namelist.bpy_type == Material:  # new Material
                ma = bpy.data.materials.new(hid)
                ma.from_fds(context, fds_namelist=fds_namelist)
                ma.use_fake_user = (
                    True
                )  # prevent deletion if used by something else (eg. PART)
                ma.set_default_appearance(context)
            elif bf_namelist.bpy_type == Scene:  # current Scene
                bf_namelist(self).from_fds(context, fds_params=fds_namelist.fds_params)
        # Set imported Scene visible
        context.window.scene = self
        # Record unmanaged namelists in free text
        te = bpy.data.texts.new(f"Imported")
        te.from_string(str(fds_case_un))
        te.current_line_index = 0
        self.bf_config_text = te
        # Set imported free text visible
        bpy.ops.scene.bf_show_text()

    def to_ge1(self, context):
        return geometry.to_ge1.scene_to_ge1(context, self)

    def set_default_appearance(self, context):  # TODO
        prefs = context.preferences.addons[__package__.split(".")[0]].preferences
        if not prefs.bf_pref_appearance:
            return
        pass

    @classmethod
    def register(cls):
        Scene.bf_namelists = cls.bf_namelists
        Scene.to_fds = cls.to_fds
        Scene.to_ge1 = cls.to_ge1
        Scene.from_fds = cls.from_fds
        Scene.set_default_appearance = cls.set_default_appearance

    @classmethod
    def unregister(cls):
        del Scene.set_default_appearance
        del Scene.from_fds
        del Scene.to_ge1
        del Scene.to_fds
        del Scene.bf_namelists


class BFCollection:
    """Extension of Blender Collection."""

    name = str()  # collection.name
    objects = list()  # collection.objects
    children = list()  # collection.children

    def to_fds(self, context):
        obs, lines = list(self.objects), list()
        obs.sort(key=lambda k: k.name)  # alphabetic by name
        if obs:
            lines.append(
                f"\n! --- Geometric namelists from Blender Collection <{self.name}>"
            )
            lines.extend(ob.to_fds(context) for ob in obs)
        lines.extend(child.to_fds(context) for child in self.children)
        return "\n".join(b for b in lines if b)  # remove empties

    @classmethod
    def register(cls):
        Collection.to_fds = cls.to_fds

    @classmethod
    def unregister(cls):
        del Collection.to_fds


# Register


def register():
    from bpy.utils import register_class

    # Blender classes
    for cls in bl_classes:
        log.debug(f"Registering Blender class <{cls.__name__}>")
        register_class(cls)
    # System parameters for tmp obs and file version
    log.debug(f"Registering sys properties: bf_is_tmp, bf_has_tmp, ...")
    Object.bf_is_tmp = BoolProperty(
        name="Is Tmp", description="Set if this Object is tmp", default=False
    )
    Object.bf_has_tmp = BoolProperty(
        name="Has Tmp",
        description="Set if this Object has tmp companions",
        default=False,
    )
    Scene.bf_file_version = IntVectorProperty(
        name="BlenderFDS File Version", size=3,
    )
    # params and namelists
    for cls in bf_params:
        cls.register()
    for cls in bf_namelists:
        cls.register()
    # Blender Object, Material, and Scene
    BFObject.register()
    BFMaterial.register()
    BFScene.register()
    BFCollection.register()


def unregister():
    from bpy.utils import unregister_class

    # Blender Object, Material, and Scene
    log.debug(f"Unregistering sys properties")
    BFObject.unregister()
    BFMaterial.unregister()
    BFScene.unregister()
    BFCollection.unregister()
    # params and namelists
    for cls in bf_namelists:
        cls.unregister()
    for cls in bf_params:
        cls.unregister()
    # System parameters for tmp obs and file version
    del Object.bf_is_tmp
    del Object.bf_has_tmp
    del Scene.bf_file_version
    # Blender classes
    for cls in bl_classes:
        log.debug(f"Unregistering Blender class <{cls.__name__}>")
        unregister_class(cls)
