import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: root
    property QtObject theme
    property QtObject motion
    property string label: ""
    property int labelWidth: 84
    property var options: []
    property string value: ""
    signal selected(string value)

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
        opacity: root.enabled ? 1 : 0.55
    }

    ComboBox {
        id: combo
        Layout.fillWidth: true
        model: root.options
        textRole: "label"
        valueRole: "value"
        enabled: root.enabled
        implicitHeight: 36
        font.pixelSize: 13
        Layout.alignment: Qt.AlignVCenter

        function indexForValue(candidate) {
            const availableOptions = root.options || []
            for (let index = 0; index < availableOptions.length; index += 1) {
                if (availableOptions[index].value === candidate)
                    return index
            }
            return -1
        }

        function currentOption() {
            const availableOptions = root.options || []
            const index = currentIndex
            if (index < 0 || index >= availableOptions.length)
                return null
            return availableOptions[index]
        }

        Component.onCompleted: currentIndex = indexForValue(root.value)

        onActivated: root.selected(currentValue)

        contentItem: Text {
            leftPadding: 12
            rightPadding: 30
            text: combo.displayText
            color: theme ? theme.textPrimary : "#1D1D1F"
            font: combo.font
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }

        ToolTip.text: {
            const option = combo.currentOption()
            return option && option.tooltip ? option.tooltip : ""
        }
        ToolTip.visible: hovered && ToolTip.text.length > 0
        ToolTip.delay: 420

        indicator: Canvas {
            x: combo.width - width - 12
            y: combo.topPadding + (combo.availableHeight - height) / 2
            width: 10
            height: 6
            rotation: combo.popup.visible ? 180 : 0
            contextType: "2d"

            Behavior on rotation {
                NumberAnimation {
                    duration: motion ? motion.stateMs : 160
                    easing.type: Easing.OutCubic
                }
            }

            onPaint: {
                context.reset()
                context.moveTo(0, 0)
                context.lineTo(width / 2, height)
                context.lineTo(width, 0)
                context.lineWidth = 1.7
                context.strokeStyle = theme ? theme.textSecondary : "#6E6E73"
                context.lineCap = "round"
                context.lineJoin = "round"
                context.stroke()
            }
        }

        Connections {
            target: root
            function onValueChanged() {
                combo.currentIndex = combo.indexForValue(root.value)
            }
            function onOptionsChanged() {
                combo.currentIndex = combo.indexForValue(root.value)
            }
        }

        background: Rectangle {
            color: theme ? theme.surfaceBase : "#FFFFFF"
            radius: theme ? theme.radiusControl : 14
            border.color: combo.activeFocus ? (theme ? theme.accentPrimary : "#0071E3") : (theme ? theme.lineSubtle : "#D2D2D7")
            border.width: 1

            Behavior on border.color {
                ColorAnimation {
                    duration: motion ? motion.stateMs : 160
                    easing.type: Easing.OutCubic
                }
            }
        }

        delegate: ItemDelegate {
            id: delegate
            required property var modelData
            width: combo.width
            height: 36

            contentItem: Text {
                leftPadding: 10
                rightPadding: 28
                text: delegate.modelData.label
                color: delegate.hovered || delegate.highlighted || combo.currentValue === delegate.modelData.value ? (theme ? theme.accentPrimary : "#0071E3") : (theme ? theme.textPrimary : "#1D1D1F")
                font.pixelSize: 13
                font.weight: combo.currentValue === delegate.modelData.value ? Font.DemiBold : Font.Normal
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }

            ToolTip.text: delegate.modelData.tooltip || ""
            ToolTip.visible: delegate.hovered && ToolTip.text.length > 0
            ToolTip.delay: 420

            background: Rectangle {
                radius: theme ? theme.radiusControl : 14
                color: delegate.hovered || delegate.highlighted || combo.currentValue === delegate.modelData.value ? (theme ? theme.accentSoft : "#EAF3FF") : (theme ? theme.surfaceBase : "#FFFFFF")

                Behavior on color {
                    ColorAnimation {
                        duration: motion ? motion.stateMs : 160
                        easing.type: Easing.OutCubic
                    }
                }
            }

            Rectangle {
                anchors.right: parent.right
                anchors.rightMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                width: 6
                height: 6
                radius: 3
                color: theme ? theme.accentPrimary : "#0071E3"
                opacity: combo.currentValue === delegate.modelData.value ? 1 : 0

                Behavior on opacity {
                    NumberAnimation {
                        duration: motion ? motion.stateMs : 160
                        easing.type: Easing.OutCubic
                    }
                }
            }
        }

        popup: Popup {
            y: combo.height + 5
            width: combo.width
            implicitHeight: Math.min(contentItem.implicitHeight + 12, 280)
            padding: 6
            opacity: 0
            scale: 0.98
            transformOrigin: Popup.Top

            contentItem: ListView {
                clip: true
                implicitHeight: contentHeight
                model: combo.popup.visible ? combo.delegateModel : null
                currentIndex: combo.highlightedIndex
                boundsBehavior: Flickable.StopAtBounds
            }

            background: Rectangle {
                color: theme ? theme.surfaceBase : "#FFFFFF"
                radius: theme ? theme.radiusCard : 18
                border.color: theme ? theme.lineSoft : "#E5E5EA"
                border.width: 1
            }

            enter: Transition {
                ParallelAnimation {
                    NumberAnimation {
                        property: "opacity"
                        from: 0
                        to: 1
                        duration: motion ? motion.panelMs : 210
                        easing.type: Easing.OutCubic
                    }
                    NumberAnimation {
                        property: "scale"
                        from: 0.98
                        to: 1
                        duration: motion ? motion.panelMs : 210
                        easing.type: Easing.OutQuint
                    }
                }
            }

            exit: Transition {
                ParallelAnimation {
                    NumberAnimation {
                        property: "opacity"
                        from: 1
                        to: 0
                        duration: motion ? motion.stateMs : 160
                        easing.type: Easing.OutCubic
                    }
                    NumberAnimation {
                        property: "scale"
                        from: 1
                        to: 0.98
                        duration: motion ? motion.stateMs : 160
                        easing.type: Easing.OutCubic
                    }
                }
            }
        }
    }
}
