#!/usr/bin/env bash 

# check for bad variables, failed commands 
set -euo pipefail 


profile_name="${1:-default}"

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" 
project_directory="$(dirname -- "$script_directory")" 
source_directory="$project_directory/src/treeangle"
data_directory="${XDG_DATA_HOME:-${HOME}/.local/share}"

# directory that QGIS sees 
plugin_directory="$data_directory/QGIS/QGIS3/profiles/$profile_name/python/plugins/treeangle"

if [[ ! -d "$source_directory" ]]; then 
    echo "plugin dir not found: "
    echo "$source_directory"
    exit 1 
fi 


mkdir -p -- "$plugin_directory"

rsync -av --exclude '__pycache__/' --exclude '*.pyc' "$source_directory/" "$plugin_directory/"

echo "TreeAngle installed in: $plugin_directory"
echo "restart qgis, then enable treeangle under [plugins] > [manage and install plugins]"
