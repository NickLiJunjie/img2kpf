import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    property QtObject theme
    property QtObject motion
    property string title: ""
    property bool expanded: true
    property string actionText: ""
    property url actionIconSource: ""
    property bool actionEnabled: true
    property int contentSpacing: theme ? theme.space16 : 16
    readonly property int outerPadding: theme ? theme.space8 : 8
    readonly property int bodyGap: theme ? theme.space8 : 8
    readonly property real bodyOpenHeight: bodyLayout.implicitHeight > 0 ? divider.height + bodyGap + bodyLayout.implicitHeight : 0
    property real bodyShownHeight: expanded ? bodyOpenHeight : 0
    default property alias contentData: bodyLayout.data
    signal toggleRequested(bool expanded)
    signal actionRequested()

    Layout.fillWidth: true
    implicitHeight: shell.implicitHeight

    Behavior on bodyShownHeight {
        NumberAnimation {
            duration: motion ? motion.sheetMs : 240
            easing.type: Easing.OutCubic
        }
    }

    Rectangle {
        id: shell
        anchors.fill: parent
        implicitHeight: headerButton.implicitHeight + root.bodyShownHeight + root.outerPadding * 2
        radius: theme ? theme.radiusCard : 18
        color: theme ? theme.bgSubtle : "#FBFBFD"
        border.color: root.expanded ? (theme ? theme.lineSubtle : "#D2D2D7") : (theme ? theme.lineSoft : "#E5E5EA")
        border.width: 1

        Behavior on border.color {
            ColorAnimation {
                duration: motion ? motion.stateMs : 160
                easing.type: Easing.OutCubic
            }
        }

        ColumnLayout {
            id: shellLayout
            anchors.fill: parent
            anchors.margins: root.outerPadding
            spacing: 0

            Rectangle {
                id: headerButton
                Layout.fillWidth: true
                implicitHeight: 32
                radius: 8
                color: toggleMouse.pressed ? (theme ? theme.accentSoft : "#EAF3FF") : "transparent"

                Behavior on color {
                    ColorAnimation {
                        duration: motion ? motion.stateMs : 160
                        easing.type: Easing.OutCubic
                    }
                }

                RowLayout {
                    anchors.fill: parent
                    spacing: theme ? theme.space8 : 8

                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                        MouseArea {
                            id: toggleMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: root.toggleRequested(!root.expanded)
                        }

                        RowLayout {
                            anchors.fill: parent
                            spacing: theme ? theme.space8 : 8

                            Canvas {
                                Layout.alignment: Qt.AlignVCenter
                                width: 12
                                height: 12
                                rotation: root.expanded ? 90 : 0
                                transformOrigin: Item.Center
                                contextType: "2d"

                                Behavior on rotation {
                                    NumberAnimation {
                                        duration: motion ? motion.stateMs : 160
                                        easing.type: Easing.OutCubic
                                    }
                                }

                                onPaint: {
                                    context.reset()
                                    context.moveTo(4, 2.5)
                                    context.lineTo(8, 6)
                                    context.lineTo(4, 9.5)
                                    context.lineWidth = 1.7
                                    context.strokeStyle = theme ? theme.textSecondary : "#6E6E73"
                                    context.lineCap = "round"
                                    context.lineJoin = "round"
                                    context.stroke()
                                }
                            }

                            Text {
                                Layout.fillWidth: true
                                text: root.title
                                color: theme ? theme.textPrimary : "#1D1D1F"
                                font.pixelSize: 13
                                font.weight: Font.DemiBold
                                verticalAlignment: Text.AlignVCenter
                            }
                        }
                    }

                    IconButton {
                        theme: root.theme
                        motion: root.motion
                        objectName: root.actionText
                        visible: root.actionText.length > 0 || root.actionIconSource.toString().length > 0
                        enabled: root.actionEnabled
                        buttonSize: 28
                        iconSize: 17
                        iconSource: root.actionIconSource
                        symbol: root.actionIconSource.toString().length > 0 ? "" : root.actionText
                        toolTipText: root.actionText
                        onClicked: root.actionRequested()
                    }
                }
            }

            Item {
                id: bodyClip
                Layout.fillWidth: true
                Layout.preferredHeight: root.bodyShownHeight
                implicitHeight: root.bodyShownHeight
                opacity: root.bodyShownHeight <= 1 ? 0 : 1
                clip: true

                Behavior on opacity {
                    NumberAnimation {
                        duration: motion ? motion.stateMs : 160
                        easing.type: Easing.OutCubic
                    }
                }

                Rectangle {
                    id: divider
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    height: 1
                    color: theme ? theme.lineSoft : "#E5E5EA"
                    opacity: 0.95
                }

                ColumnLayout {
                    id: bodyLayout
                    width: parent.width
                    y: divider.height + root.bodyGap
                    spacing: root.contentSpacing
                }
            }
        }
    }
}
