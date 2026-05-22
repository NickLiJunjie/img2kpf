import QtQuick

QtObject {
    property bool reducedMotion: false
    readonly property int pressMs: reducedMotion ? 0 : 110
    readonly property int stateMs: reducedMotion ? 0 : 160
    readonly property int panelMs: reducedMotion ? 0 : 210
    readonly property int sheetMs: reducedMotion ? 0 : 240
    readonly property int easingOut: Easing.OutCubic
    readonly property int easingSoft: Easing.OutQuint
}
