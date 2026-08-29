""" QGIS plugin entrypoint 
    
    required by pythons import system 
    and allows QGIS to load TreeAnglePlugin 
"""

def classFactory(iface):
    from .plugin import TreeAnglePlugin as TAP
    return TAP(iface)

