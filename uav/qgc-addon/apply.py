"""Apply the reviewable Linghang QGC v5.1.5 data-bridge changes to a local checkout."""
import argparse
import shutil
from pathlib import Path


def replace(file, old, new):
    text = file.read_text(encoding='utf-8')
    if new in text:
        return
    if old not in text:
        raise ValueError(f'QGC source does not match the verified v5.1.5 integration point: {file.name}')
    file.write_text(text.replace(old, new, 1), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    root = args.source.resolve(); module = root / 'src/MissionManager'
    if not (module / 'PlanMasterController.h').is_file():
        raise ValueError('Not a QGC source checkout')
    for name in ['LinghangBridge.h', 'LinghangBridge.cc']:
        shutil.copyfile(Path(__file__).parent / name, module / name)
    replace(module / 'CMakeLists.txt', '    PRIVATE\n', '    PRIVATE\n        LinghangBridge.h\n        LinghangBridge.cc\n')
    replace(module / 'PlanMasterController.h', '    QJsonDocument saveToJson();', '    QJsonDocument saveToJson();\n    bool loadLinghangDraft(const QJsonObject& plan, QString& error);')
    implementation = '''
bool PlanMasterController::loadLinghangDraft(const QJsonObject& plan, QString& error)
{
    if (syncInProgress()) {
        error = tr("Plan synchronization is in progress");
        return false;
    }
    const QByteArray previous = saveToJson().toJson(QJsonDocument::Compact);
    if (!_loadPlanJson(QJsonDocument(plan).toJson(QJsonDocument::Compact), error)) {
        QString restoreError;
        _loadPlanJson(previous, restoreError);
        return false;
    }
    _clearCurrentPlanFile();
    _setDirtyStates(true, true);
    return true;
}
'''
    file = module / 'PlanMasterController.cc'; text = file.read_text(encoding='utf-8')
    if 'bool PlanMasterController::loadLinghangDraft' not in text:
        file.write_text(text + implementation, encoding='utf-8')
    qml = root / 'src/PlanView/PlanView.qml'
    replace(qml, 'import QGroundControl\n', 'import QGC\nimport QGroundControl\n')
    overlay = '''
    LinghangBridge {
        id: linghangBridge
        controller: planMasterController
    }
    Column {
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        anchors.margins: ScreenTools.defaultFontPixelHeight
        spacing: 4
        z: 1000
        QGCTextField {
            id: linghangAltitude
            width: ScreenTools.defaultFontPixelWidth * 28
            placeholderText: qsTr("实际起飞点海拔（米），请明确填写")
        }
        QGCButton {
            text: linghangBridge.picking ? qsTr("请在地图选点（点击此处取消）") : qsTr("为语音助手选择实际起飞位置")
            onClicked: linghangBridge.picking = !linghangBridge.picking
        }
        QGCLabel { text: linghangBridge.statusText; font.pointSize: ScreenTools.smallFontPointSize }
    }
'''
    replace(qml, '    id: _root\n', '    id: _root\n' + overlay)
    marker = '                switch (_editingLayer) {'
    replace(qml, marker, '''                if (linghangBridge.picking) {
                    linghangBridge.selectPosition(coordinate, linghangAltitude.text)
                    return
                }

''' + marker)
    print('Applied Linghang data bridge. No upload, arm, mode, or takeoff route is exposed.')


if __name__ == '__main__':
    main()
