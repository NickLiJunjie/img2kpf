import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: root
    property QtObject theme
    property QtObject motion
    property string label: ""
    property int labelWidth: 84
    property real value: 0
    property real from: 0
    property real to: 1
    property real stepSize: 1
    property string displayValue: Math.round(value).toString()
    signal valueEdited(real value)
    signal resetClicked()

    spacing: theme ? theme.space8 : 8

    Text {
        text: root.label
        color: theme ? theme.textSecondary : "#6E6E73"
        font.pixelSize: 13
        font.weight: Font.Medium
        Layout.preferredWidth: root.labelWidth
        horizontalAlignment: Text.AlignRight
        elide: Text.ElideRight
        verticalAlignment: Text.AlignVCenter
        Layout.alignment: Qt.AlignVCenter
    }

    Slider {
        id: slider
        Layout.fillWidth: true
        from: root.from
        to: root.to
        stepSize: root.stepSize
        value: root.value
        live: true
        implicitHeight: 30
        onMoved: root.valueEdited(value)

        background: Rectangle {
            x: slider.leftPadding
            y: slider.topPadding + slider.availableHeight / 2 - height / 2
            width: slider.availableWidth
            height: 4
            radius: 2
            color: theme ? theme.lineSoft : "#E5E5EA"

            Rectangle {
                width: slider.visualPosition * parent.width
                height: parent.height
                radius: 2
                color: theme ? theme.accentPrimary : "#0071E3"
            }
        }

        handle: Rectangle {
            x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
            y: slider.topPadding + slider.availableHeight / 2 - height / 2
            width: slider.pressed ? 17 : 15
            height: width
            radius: width / 2
            color: theme ? theme.surfaceBase : "#FFFFFF"
            border.color: theme ? theme.accentPrimary : "#0071E3"
            border.width: 2

            Behavior on width {
                NumberAnimation {
                    duration: motion ? motion.pressMs : 110
                    easing.type: Easing.OutCubic
                }
            }
        }
    }

    Text {
        text: root.displayValue
        color: theme ? theme.textPrimary : "#1D1D1F"
        font.pixelSize: 13
        horizontalAlignment: Text.AlignRight
        Layout.preferredWidth: 38
        Layout.alignment: Qt.AlignVCenter
    }

    Button {
        implicitWidth: 30
        implicitHeight: 30
        padding: 0
        text: "↺"
        font.pixelSize: 14
        onClicked: root.resetClicked()

        contentItem: Text {
            text: parent.text
            color: theme ? theme.textSecondary : "#6E6E73"
            font: parent.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        background: Rectangle {
            radius: 10
            color: parent.hovered ? (theme ? theme.bgSubtle : "#FBFBFD") : (theme ? theme.surfaceBase : "#FFFFFF")
            border.color: theme ? theme.lineSoft : "#E5E5EA"
            border.width: 1
        }
    }
}
