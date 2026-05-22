import QtQuick
import QtQuick.Controls

Button {
    id: root
    property QtObject theme
    property QtObject motion
    property string symbol: ""
    property url iconSource: ""
    property bool toggled: false
    property string tone: "neutral"
    property int buttonSize: 38
    property int iconSize: 20
    property int buttonRadius: Math.min(10, Math.round(buttonSize * 0.32))
    property string toolTipText: ""
    readonly property bool hasIcon: iconSource.toString().length > 0

    implicitWidth: buttonSize
    implicitHeight: buttonSize
    hoverEnabled: true
    padding: 0
    scale: down ? 0.94 : 1.0

    Behavior on scale {
        NumberAnimation {
            duration: motion ? motion.pressMs : 110
            easing.type: Easing.OutCubic
        }
    }

    contentItem: Item {
        Image {
            anchors.centerIn: parent
            width: root.iconSize
            height: root.iconSize
            sourceSize.width: root.iconSize
            sourceSize.height: root.iconSize
            source: root.iconSource
            visible: root.hasIcon
            opacity: root.enabled ? 1 : 0.45
        }

        Text {
            anchors.centerIn: parent
            visible: !root.hasIcon
            text: root.symbol
            color: {
                if (!root.enabled)
                    return theme ? theme.textTertiary : "#8E8E93"
                if (root.tone === "danger")
                    return theme ? theme.dangerBase : "#D92D20"
                return theme ? theme.textPrimary : "#1D1D1F"
            }
            font.pixelSize: 17
            font.weight: Font.DemiBold
            rotation: root.toggled ? 180 : 0

            Behavior on rotation {
                NumberAnimation {
                    duration: motion ? motion.panelMs : 210
                    easing.type: Easing.OutCubic
                }
            }
        }
    }

    ToolTip {
        id: iconToolTip
        visible: root.hovered && root.toolTipText.length > 0
        delay: 420
        timeout: 5000
        x: Math.round((root.width - implicitWidth) / 2)
        y: -implicitHeight - 8
        padding: 0
        text: root.toolTipText

        contentItem: Text {
            text: iconToolTip.text
            color: theme ? theme.textPrimary : "#1D1D1F"
            font.pixelSize: 12
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            leftPadding: 12
            rightPadding: 12
            topPadding: 8
            bottomPadding: 8
        }

        background: Rectangle {
            radius: 10
            color: theme ? theme.surfaceBase : "#FFFFFF"
            border.color: theme ? theme.lineSubtle : "#D2D2D7"
            border.width: 1
        }
    }

    background: Rectangle {
        radius: root.buttonRadius
        color: {
            if (root.tone === "danger" && root.hovered && root.enabled)
                return theme ? theme.dangerSoft : "#FDECEC"
            if (root.hovered || root.checked)
                return theme ? theme.bgSubtle : "#FBFBFD"
            return "transparent"
        }
        border.color: {
            if (root.tone === "danger")
                return root.hovered || root.checked ? (theme ? theme.dangerBase : "#D92D20") : (theme ? theme.lineSoft : "#E5E5EA")
            return root.checked ? (theme ? theme.accentPrimary : "#0071E3") : (theme ? theme.lineSoft : "#E5E5EA")
        }
        border.width: root.hovered || root.checked ? 1 : 0

        Behavior on color {
            ColorAnimation {
                duration: motion ? motion.stateMs : 160
                easing.type: Easing.OutCubic
            }
        }

        Behavior on border.color {
            ColorAnimation {
                duration: motion ? motion.stateMs : 160
                easing.type: Easing.OutCubic
            }
        }
    }
}
