import QtQuick
import QtQuick.Controls

TextField {
    id: root
    property QtObject theme
    property QtObject motion
    property bool revealEnd: false

    implicitHeight: 36
    leftPadding: 12
    rightPadding: 12
    color: theme ? theme.textPrimary : "#1D1D1F"
    placeholderTextColor: theme ? theme.textTertiary : "#8E8E93"
    selectedTextColor: "#FFFFFF"
    selectionColor: theme ? theme.accentPrimary : "#0071E3"
    font.pixelSize: 13
    verticalAlignment: TextInput.AlignVCenter
    selectByMouse: true
    horizontalAlignment: revealEnd && text.length > 0 && !activeFocus ? TextInput.AlignRight : TextInput.AlignLeft
    ToolTip.text: text
    ToolTip.visible: hovered && text.length > 0
    ToolTip.delay: 420

    background: Rectangle {
        radius: theme ? theme.radiusControl : 14
        color: theme ? theme.surfaceBase : "#FFFFFF"
        border.color: root.activeFocus ? (theme ? theme.accentPrimary : "#0071E3") : (theme ? theme.lineSubtle : "#D2D2D7")
        border.width: 1

        Behavior on border.color {
            ColorAnimation {
                duration: motion ? motion.stateMs : 160
                easing.type: Easing.OutCubic
            }
        }
    }
}
