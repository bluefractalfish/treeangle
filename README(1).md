# TreeAngle

TreeAngle is a QGIS plugin for marking fallen trees in aerial imagery. It records tree length, crown width, fall direction, and damage attributes in GeoPackage layers.

## Install

TreeAngle requires QGIS 3.28 or newer.

Copy the complete plugin folder to:

```text
~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/treeangle/
```

Restart QGIS, then enable **TreeAngle** under **Plugins > Manage and Install Plugins**.

## First use

1. Load the raster imagery you want to annotate.
2. Click **OPEN GPKG** and choose where to save the annotations.
3. Click **CREATE CLASS**, enter a short name and the damage attributes, then save it.
4. Select that class from the toolbar.
5. Click **ANNOTATE**.

## Draw a tree

Click four points in this order:

1. Base or trunk break
2. Tree tip
3. Left edge of the crown
4. Right edge of the crown

The first two points define tree length and fall direction. The last two define crown width. After the fourth click, the kite and fall vector are saved and the tool is ready for the next tree.

- Right-click or press **Backspace** to undo the last point.
- Press **Escape** to cancel the current kite.

## Damage classes

A damage class is a reusable set of attributes. The selected class is added to every new tree until you choose another one.

- **CREATE CLASS** creates a class.
- **EDIT CLASS** changes the saved class template.
- **DELETE CLASS** removes the template. Existing annotations are not deleted.
- **APPLY CLASS** assigns the active class to selected kites.

Editing a class does not automatically change trees already annotated with it. Select those trees and use **APPLY CLASS** if they should receive the new values.

## Select and edit trees

Use **SELECT KITES** to select one or more existing annotations. Hold `Ctrl` to add trees to the selection.

Use **EDIT POINTS** to drag a kite point to a new position. Measurements and the fall vector are updated when the point is released.

The **Tree History** panel lists each tree's height, crown width, damage class, and creation time. Click a row to select and center that tree on the map.

## Optional fields

The second toolbar contains exposure, confidence, tree type, ground type, and health fields.

To update existing trees:

1. Select the kites.
2. Choose the values to change.
3. Leave all other fields set to **KEEP**.
4. Click **APPLY FIELDS**.

## Output

TreeAngle creates two related GeoPackages:

- The annotation layer contains the kite geometry, measurements, damage attributes, and source information.
- The fall-vector layer contains a line from the base to the tree tip.

Both layers use the same `tree_id` to identify a tree.
