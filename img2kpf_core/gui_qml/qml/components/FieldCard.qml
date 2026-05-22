import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    property QtObject theme
    property QtObject motion
    property string label: ""
    property string metaText: ""
    property string helperText: ""
    default property alias contentData: contentLayout.data

    Layout.fillWidth: true
    implicitHeight: bodyLayout.implicitHeight + (theme ? theme.space12 * 2 : 24)
    radius: theme ? theme.radiusCard - 2 : 16
    color: theme ? theme.surfaceBase : "#FFFFFF"
    border.color: theme ? theme.lineSoft : "#E5E5EA"
    border.width: 1

    Behavior on border.color {
        ColorAnimation {
            duration: motion ? motion.stateMs : 160
            easing.type: Easing.OutCubic
        }
    }

    ColumnLayout {
        id: bodyLayout
        anchors.fill: parent
        anchors.margins: theme ? theme.space12 : 12
        spacing: theme ? theme.space8 : 8

        RowLayout {
            Layout.fillWidth: true
            spacing: theme ? theme.space8 : 8

            Text {
                text: root.label
                color: theme ? theme.textSecondary : "#6E6E73"
                font.pixelSize: 12
                font.weight: Font.DemiBold
                verticalAlignment: Text.AlignVCenter
            }

            Item {
                Layout.fillWidth: true
            }

            Text {
                visible: root.metaText.length > 0
                Layout.preferredWidth: 128
                text: root.metaText
                color: theme ? theme.textTertiary : "#8E8E93"
                font.pixelSize: 11
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideLeft
                verticalAlignment: Text.AlignVCenter
            }
        }

        Text {
            visible: root.helperText.length > 0
            Layout.fillWidth: true
            text: root.helperText
            color: theme ? theme.textTertiary : "#8E8E93"
            font.pixelSize: 11
            wrapMode: Text.WordWrap
        }

        ColumnLayout {
            id: contentLayout
            Layout.fillWidth: true
            spacing: theme ? theme.space8 : 8
        }
    }
}
