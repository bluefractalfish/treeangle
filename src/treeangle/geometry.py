""" 
crs aware kite measurment adapter
"""


from __future__ import annotations 
from dataclasses import dataclass 
from math import atan2, degrees, hypot 

from qgis.core import (
        Qgis, 
        QgsCoordinateReferenceSystem, 
        QgsDistanceArea, 
        QgsGeometry, 
        QgsPointXY, 
        QgsProject, 
        )

# controls how large a tree we acan annotate 
MIN_AXIS_LENGTH_M = 0.05
MIN_KITE_AREA_SQM = 0.0001 


@dataclass(frozen=True, slots=True)
class Point: 
    """ minimal x/y point used by domain layer"""
    x: float 
    y: float 


@dataclass(frozen=True, slots=True)
class KiteMetrics:
    """ physical measurements derived from one kite annotation"""

    tree_height_m: float 
    crown_width_m: float 
    fall_azimuth_deg: float 
    wind_from: float 
    kite_area_sqm: float 
    fall_direction: str 

@dataclass(frozen=True, slots=True)
class Kite: 
    """ p0: tree base, p1: tree tip, p2,3: left/right crown edges 
        
        major axis: p0->p1, minor axis p2->p3, and polygon ring is p0-p2-p1-p3-p0

    """
    p0: Point 
    p1: Point 
    p2: Point
    p3: Point 
    
    @property
    def points(self) -> tuple[Point,Point,Point,Point]:
        return self.p0, self.p1, self.p2, self.p3 

    @property
    def ring(self) ->  tuple[Point, Point, Point, Point, Point]:
        return self.p0, self.p2, self.p1, self.p3, self.p0 

    @property
    def height(self) -> float:
        return _distance(self.p0, self.p1)

    @property 
    def width(self) -> float: 
        return _distance(self.p2, self.p3)
    @property 
    def area(self) -> float: 
        return abs(_signed_area(self.ring))
    @property 
    def azimuth(self) -> float: 
        dx = self.p1.x - self.p0.x 
        dy = self.p1.y - self.p0.y 
        return (degrees(atan2(dx,dy)) + 360.0) % 360 

    def validate(self, min_axis_length: float = 0.0) -> None:
        """ reject duplicate, flat, crossed, or incorrectly ordered kites"""

        if len(set(self.points)) != 4:
            raise ValueError("the four kite points bust be distinct!")

        major_length = self.height 
        minor_length = self.width 

        if major_length <= min_axis_length: 
            raise ValueError("the base and tip are too close...")
        if minor_length <= min_axis_length: 
            raise ValueError("the left and right edge are too close...")

        coordinate_scale = max(major_length, minor_length)
        cross_tolerance = max(coordinate_scale * coordinate_scale * 1.0e-12, 1.0e-30)

        r_side = _cross(self.p0, self.p1, self.p2)
        l_side = _cross(self.p0,self.p1, self.p3) 

        if abs(l_side) <= cross_tolerance or abs(r_side) <= cross_tolerance:
            raise ValueError("major minor axis cannot be parallell")
        if l_side * r_side >= 0.0:
            raise ValueError("crown must span major axis")
        
        intersection = _segment_intersection(self.p0, self.p1, self.p2, self.p3) 
        if intersection is None: 
            raise ValueError("major and minor axes cannot be parallel")

        major_frac, minor_frac = intersection 
        tolerance = 1.0e-9 
        if not(
                tolerance < major_frac < 1.0 - tolerance 
                and tolerance < minor_frac < 1.0 -tolerance
                ): 
            raise ValueError(" the major and minor axes must cross inside both axes")

        area_tolerance = max(coordinate_scale * coordinate_scale * 1.0e-12, 1.0e-30)

        if self.area <= area_tolerance: 
            raise ValueError(" the kite is too flat to form a polygon ")

        
def compass_direction(azimuth_degrees: float) -> str:
    """return the nearest 16-point compass label """

    labels = (
        "N",
        "NNE",
        "NE",
        "ENE",
        "E",
        "ESE",
        "SE",
        "SSE",
        "S",
        "SSW",
        "SW",
        "WSW",
        "W",
        "WNW",
        "NW",
        "NNW",
    )
    index = int(((azimuth_degrees % 360.0) + 11.25) // 22.5) % 16
    return labels[index]


def _distance(a: Point, b: Point) -> float:
    return hypot(b.x - a.x, b.y - a.y)


def _cross(a: Point, b: Point, c: Point) -> float: 
    """ 2d cross product of vectors ab and ac """

    return (
            (b.x - a.x) * (c.y - a.y) 
                - (b.y - a.y) * (c.x - a.x)
            )

def _segment_intersection(
        a: Point, 
        b: Point, 
        c: Point, 
        d: Point, 
        ) -> tuple[float, float] | None: 
    """ return fractions along AB and CD where thir infinite lines cross"""
    
    ax = a.x 
    ay = a.y 
    bx = b.x 
    by = b.y 
    cx = c.x 
    cy = c.y 
    dx = d.x 
    dy = d.y 

    rx = bx - ax 
    ry = by - ay 
    sx = dx - cx 
    sy = dy - cy 
    denom = rx * sy - ry * sx 
    scale = max(hypot(rx, ry) * hypot(sx, sy), 1.0e-30)
    if abs(denom) <= scale * 1.0e-12: 
        return None 
    qpx = cx - ax 
    qpy = cy - ay 
    maj_frac = (qpx * sy - qpy * sx) / denom 
    min_frac = (qpx * ry - qpy * rx) / denom 

    return maj_frac, min_frac 
    
def _signed_area(ring: tuple[Point, ...]) -> float:  
    aa = 0.0 
    for current, following in zip(ring, ring[1:]): 
        aa += current.x * following.y - following.x * current.y 
    return aa / 2.0


def measure(
        base: QgsPointXY, 
        tip: QgsPointXY, 
        left: QgsPointXY, 
        right: QgsPointXY, 
        crs: QgsCoordinateReferenceSystem, 
        ) -> KiteMetrics: 
    """ public front to validate, measure 4 points in qgis layer crs """

    kite = Kite(*(_domain_point(point) for point in (base, tip, left, right)))
    
    kite.validate(min_axis_length=0.0) 

    calculator = _distance_calculator(crs)
    
    tree_height_m = _in_metres(calculator.measureLine(base, tip), calculator)
    crown_width_m = _in_metres(calculator.measureLine(left, right), calculator)
  
    if tree_height_m < MIN_AXIS_LENGTH_M:
        raise ValueError(
                f"minimum tree height is {MIN_AXIS_LENGTH_M:.2f} m"
                )
    if crown_width_m < MIN_AXIS_LENGTH_M: 
        raise ValueError(
                f"minimum crown width is {MIN_AXIS_LENGTH_M} m"
                )
    polygon = QgsGeometry.fromPolygonXY([[base, left, tip, right, base]])
    if polygon.isEmpty():
        raise ValueError("the four points to do not form a polygon")

    kite_area_sqm = _area_sqm(calculator.measureArea(polygon), calculator) 
    if kite_area_sqm < MIN_KITE_AREA_SQM:
        raise ValueError(
                f"kite area is {kite_area_sqm:.4f}, which is too small to store reliably"
                )

    azimuth = (
            degrees(calculator.bearing(base, tip))
            % 360 
            )

    
    return KiteMetrics(
            tree_height_m=tree_height_m, 
            crown_width_m=crown_width_m, 
            fall_azimuth_deg=azimuth, 
            wind_from=(azimuth + 180.0) % 360.0, 
            kite_area_sqm = kite_area_sqm, 
            fall_direction=compass_direction(azimuth)
            )

    #tree_height_m = _length_m(calculator.measureLine(base, tip))


def _distance_calculator(crs: QgsCoordinateReferenceSystem) -> QgsDistanceArea: 
    project = QgsProject.instance() 
    if project is None: 
        raise ValueError 

    calculator = QgsDistanceArea() 
    calculator.setSourceCrs(
            crs, 
            project.transformContext()
            )

    ellipsoid = project.ellipsoid()
    if not ellipsoid or ellipsoid == "NONE":
        ellipsoid = crs.ellipsoidAcronym()

    if ellipsoid and ellipsoid != "NONE": 
        calculator.setEllipsoid(ellipsoid) 

    return calculator

def _in_metres(value: float, calculator: QgsDistanceArea) -> float:
    return calculator.convertLengthMeasurement(value, Qgis.DistanceUnit.Meters)

def _area_sqm(value: float, calculator: QgsDistanceArea) -> float: 
    return calculator.convertAreaMeasurement(value, Qgis.AreaUnit.SquareMeters)

def _domain_point(point: QgsPointXY) -> Point: 
    return Point(point.x(), point.y())

