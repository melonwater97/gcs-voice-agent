#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd "$(dirname "$0")/../.." && pwd)"
build_root="${LINGHANG_QGC_BUILD_ROOT:-$HOME/.cache/linghang-qgc}"
mkdir -p "$build_root/source"
if [ ! -d "$build_root/source/.git" ]; then
    cp -a "$project_root/third_party/qgroundcontrol/.git" "$build_root/source/.git"
fi
tar -C "$project_root/third_party/qgroundcontrol" --exclude=.git -cf - . | tar -C "$build_root/source" -xf -
export PATH="$project_root/uav/.venv-wsl/bin:/usr/local/bin:/usr/bin:/bin"
cd "$build_root/source"
mapfile -t dependency_args < <(python "$project_root/uav/qgc-addon/prepare_dependencies.py" "$project_root/desktop/.data/qgc-deps" "$build_root/dependencies")
python tools/configure.py -B build --release --testing --unity --unity-batch 4 \
  --qt-root "$build_root/qt/6.11.1/gcc_64" -- \
  -DQGC_APP_NAME=LinghangQGC -DQGC_ENABLE_GST_VIDEOSTREAMING=OFF -DCMAKE_AUTOGEN_PARALLEL=2 \
  -DPython3_EXECUTABLE="$project_root/uav/.venv-wsl/bin/python" "${dependency_args[@]}"
cmake --build build --parallel 2
