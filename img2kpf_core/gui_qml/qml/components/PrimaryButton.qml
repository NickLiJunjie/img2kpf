import QtQuick
import QtQuick.Controls

Button {
    id: root
    property QtObject theme
    property QtObject motion
    property bool prominent: true
    property string tone: "accent"

    implicitHeight: 36
    leftPadding: 16
    rightPadding: 16
    font.pixelSize: 13
    font.weight: Font.DemiBold
    scale: down ? 0.985 : 1.0

    Behavior on scale {
        NumberAnimation {
            duration: motion ? motion.pressMs : 110
            easing.type: Easing.OutCubic
        }
    }

    contentItem: Text {
        text: root.text
        color: {
            if (!root.enabled)
                return root.theme ? root.theme.textTertiary : "#8E8E93"
            if (root.prominent)
                return "#FFFFFF"
            if (root.tone === "danger")
                return root.theme ? root.theme.dangerBase : "#D92D20"
            return root.theme ? root.theme.textPrimary : "#1D1D1F"
        }
        font: root.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        radius: theme ? theme.radiusControl : 14
        color: {
            if (!root.enabled)
                return theme ? theme.lineSoft : "#E5E5EA"
            if (!root.prominent)
                return root.hovered ? (root.tone === "danger" ? (theme ? theme.dangerSoft : "#FDECEC") : (theme ? theme.bgSubtle : "#FBFBFD")) : (theme ? theme.surfaceBase : "#FFFFFF")
            return root.hovered ? (theme ? theme.accentHover : "#0062C4") : (theme ? theme.accentPrimary : "#0071E3")
        }
        border.width: root.prominent ? 0 : 1
        border.color: root.tone === "danger" ? (theme ? theme.dangerBase : "#D92D20") : (theme ? theme.lineSubtle : "#D2D2D7")

        Behavior on color {
            ColorAnimation {
                duration: motion ? motion.stateMs : 160
                easing.type: Easing.OutCubic
            }
        }
    }
}
