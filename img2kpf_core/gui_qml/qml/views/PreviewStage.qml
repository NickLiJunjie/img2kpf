import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

SquircleCard {
    id: root
    property QtObject theme
    property QtObject motion
    property bool runnable: false
    property string titleText: "Live Preview"
    property string placeholderText: "Live preview wiring comes next. This shell locks the visual system first."
    property string imageSource: ""
    property bool busy: false
    property string busyText: ""
    property bool showCropBoxes: false
    property bool canGoLeft: false
    property bool canGoRight: false
    property string pageText: "—"
    property int currentPageNumber: 0
    property int totalPages: 0
    property string cropBoxText: "Show crop box"
    property string pageJumpText: ""
    property string volumeText: "Volume"
    property var volumeOptions: []
    property string volumeValue: ""
    property string leftActionText: ""
    property string rightActionText: ""
    property real previewAspectRatio: 0.72
    property bool coverWatermarkVisible: false
    property string coverWatermarkText: "Cover"
    property string coverWatermarkToolTip: ""
    property string coverWatermarkSide: "left"

    signal leftRequested()
    signal rightRequested()
    signal pageJumpRequested(string pageNumber)
    signal volumeSelected(string volume)
    signal cropBoxesToggled()

    cardRadius: theme ? theme.radiusStage : 24
    cardColor: theme ? theme.surfaceBase : "#FFFFFF"
    strokeColor: theme ? theme.lineSoft : "#E5E5EA"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: theme ? theme.space24 : 24
        spacing: theme ? theme.space16 : 16

        RowLayout {
            Layout.fillWidth: true
            spacing: theme ? theme.space12 : 12

            Text {
                Layout.fillWidth: true
                text: root.titleText
                color: theme ? theme.textPrimary : "#1D1D1F"
                font.pixelSize: 20
                font.weight: Font.DemiBold
            }

            ComboBox {
                id: volumeCombo
                visible: (root.volumeOptions || []).length > 1
                Layout.preferredWidth: 170
                implicitHeight: 36
                model: root.volumeOptions
                textRole: "label"
                valueRole: "value"
                font.pixelSize: 12

                function indexForValue(candidate) {
                    const availableOptions = root.volumeOptions || []
                    for (let index = 0; index < availableOptions.length; index += 1) {
                        if (availableOptions[index].value === candidate)
                            return index
                    }
                    return -1
                }

                Component.onCompleted: currentIndex = indexForValue(root.volumeValue)
                onActivated: root.volumeSelected(currentValue)

                Connections {
                    target: root
                    function onVolumeValueChanged() {
                        volumeCombo.currentIndex = volumeCombo.indexForValue(root.volumeValue)
                    }
                    function onVolumeOptionsChanged() {
                        volumeCombo.currentIndex = volumeCombo.indexForValue(root.volumeValue)
                    }
                }

                contentItem: Text {
                    leftPadding: 12
                    rightPadding: 28
                    text: volumeCombo.displayText || root.volumeText
                    color: theme ? theme.textPrimary : "#1D1D1F"
                    font: volumeCombo.font
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }

                indicator: Canvas {
                    x: volumeCombo.width - width - 13
                    y: volumeCombo.topPadding + (volumeCombo.availableHeight - height) / 2
                    width: 10
                    height: 6
                    rotation: volumeCombo.popup.visible ? 180 : 0
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

                background: Rectangle {
                    radius: 999
                    color: theme ? theme.bgSubtle : "#FBFBFD"
                    border.color: volumeCombo.activeFocus ? (theme ? theme.accentPrimary : "#0071E3") : (theme ? theme.lineSoft : "#E5E5EA")
                    border.width: 1
                }

                delegate: ItemDelegate {
                    id: volumeDelegate
                    required property var modelData
                    width: volumeCombo.width
                    height: 34

                    contentItem: Text {
                        leftPadding: 10
                        rightPadding: 26
                        text: volumeDelegate.modelData.label
                        color: volumeCombo.currentValue === volumeDelegate.modelData.value ? (theme ? theme.accentPrimary : "#0071E3") : (theme ? theme.textPrimary : "#1D1D1F")
                        font.pixelSize: 12
                        font.weight: volumeCombo.currentValue === volumeDelegate.modelData.value ? Font.DemiBold : Font.Normal
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                    }
                }

                popup: Popup {
                    y: volumeCombo.height + 5
                    width: volumeCombo.width
                    implicitHeight: Math.min(contentItem.implicitHeight + 12, 240)
                    padding: 6

                    contentItem: ListView {
                        clip: true
                        implicitHeight: contentHeight
                        model: volumeCombo.popup.visible ? volumeCombo.delegateModel : null
                        currentIndex: volumeCombo.highlightedIndex
                        boundsBehavior: Flickable.StopAtBounds
                    }

                    background: Rectangle {
                        color: theme ? theme.surfaceBase : "#FFFFFF"
                        radius: theme ? theme.radiusCard : 18
                        border.color: theme ? theme.lineSoft : "#E5E5EA"
                        border.width: 1
                    }
                }
            }
        }

        Rectangle {
            id: stageSurface
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: theme ? theme.radiusStage : 24
            color: theme && theme.dark ? "#151820" : "#EEF0F5"
            border.color: theme ? theme.lineSoft : "#E5E5EA"
            border.width: 1
            antialiasing: true
            clip: true

            Item {
                id: previewHost
                anchors.fill: parent
                anchors.margins: theme ? theme.space24 : 24
                visible: root.imageSource.length > 0

                Item {
                    id: deviceFrame
                    readonly property real safeRatio: Math.max(0.2, Math.min(root.previewAspectRatio, 4.0))
                    readonly property real hostRatio: previewHost.width / Math.max(1, previewHost.height)
                    width: hostRatio > safeRatio ? previewHost.height * safeRatio : previewHost.width
                    height: hostRatio > safeRatio ? previewHost.height : previewHost.width / safeRatio
                    anchors.centerIn: parent

                    Behavior on width {
                        NumberAnimation {
                            duration: motion ? motion.panelMs : 210
                            easing.type: Easing.OutCubic
                        }
                    }

                    Behavior on height {
                        NumberAnimation {
                            duration: motion ? motion.panelMs : 210
                            easing.type: Easing.OutCubic
                        }
                    }

                    Image {
                        anchors.fill: parent
                        source: root.imageSource
                        fillMode: Image.PreserveAspectFit
                        asynchronous: true
                        cache: false
                        smooth: true
                        mipmap: true
                    }

                    Rectangle {
                        id: coverWatermark
                        visible: root.coverWatermarkVisible
                        x: Math.min(parent.width - width - 14, root.coverWatermarkSide === "right" ? parent.width / 2 + 14 : 14)
                        y: 14
                        width: coverWatermarkLabel.implicitWidth + 24
                        height: 32
                        radius: 10
                        color: theme && theme.dark ? "#802A6DF4" : "#CCEFF6FF"
                        border.color: theme ? theme.accentPrimary : "#0071E3"
                        border.width: 1
                        opacity: 0.78

                        Text {
                            id: coverWatermarkLabel
                            anchors.centerIn: parent
                            text: root.coverWatermarkText
                            color: theme && theme.dark ? "#FFFFFF" : (theme ? theme.accentPrimary : "#0071E3")
                            font.pixelSize: 13
                            font.weight: Font.DemiBold
                        }

                        HoverHandler {
                            id: coverWatermarkHover
                            cursorShape: Qt.PointingHandCursor
                        }

                        ToolTip.text: root.coverWatermarkToolTip
                        ToolTip.visible: coverWatermarkHover.hovered && root.coverWatermarkToolTip.length > 0
                        ToolTip.delay: 360
                    }
                }
            }

            Rectangle {
                anchors.centerIn: parent
                visible: root.busy && root.imageSource.length > 0
                radius: 999
                color: theme ? theme.surfaceBase : "#FFFFFF"
                border.color: theme ? theme.lineSoft : "#E5E5EA"
                border.width: 1
                opacity: 0.9
                implicitWidth: busyLabel.implicitWidth + 28
                implicitHeight: 36

                Text {
                    id: busyLabel
                    anchors.centerIn: parent
                    text: root.busyText
                    color: theme ? theme.textSecondary : "#6E6E73"
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
            }

            Column {
                anchors.centerIn: parent
                spacing: theme ? theme.space12 : 12
                visible: root.imageSource.length === 0

                Rectangle {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: 220
                    height: 312
                    radius: 18
                    color: theme ? theme.surfaceBase : "#FFFFFF"
                    border.color: theme ? theme.lineSubtle : "#D2D2D7"
                    border.width: 1
                    scale: root.runnable ? 1.015 : 1.0

                    Behavior on scale {
                        NumberAnimation {
                            duration: motion ? motion.panelMs : 210
                            easing.type: Easing.OutCubic
                        }
                    }

                    Rectangle {
                        anchors.centerIn: parent
                        width: 150
                        height: 230
                        radius: 12
                        color: theme ? theme.bgBase : "#F5F5F7"
                    }
                }

                Text {
                    width: 360
                    text: root.placeholderText
                    color: theme ? theme.textTertiary : "#8E8E93"
                    font.pixelSize: 13
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 40
            visible: root.imageSource.length > 0

            Rectangle {
                anchors.centerIn: parent
                width: pagerRow.implicitWidth + 12
                height: 36
                radius: 999
                color: theme ? theme.surfaceBase : "#FFFFFF"
                border.color: theme ? theme.lineSoft : "#E5E5EA"
                border.width: 1

                Row {
                    id: pagerRow
                    anchors.centerIn: parent
                    spacing: 2

                    Button {
                        id: leftPageButton
                        width: 32
                        height: 30
                        enabled: root.canGoLeft
                        text: "‹"
                        scale: down ? 0.94 : 1.0
                        ToolTip.text: root.leftActionText
                        ToolTip.visible: hovered && root.leftActionText.length > 0
                        onClicked: root.leftRequested()

                        Behavior on scale {
                            NumberAnimation {
                                duration: motion ? motion.pressMs : 110
                                easing.type: Easing.OutCubic
                            }
                        }

                        contentItem: Text {
                            text: leftPageButton.text
                            color: leftPageButton.enabled ? (theme ? theme.textPrimary : "#1D1D1F") : (theme ? theme.textTertiary : "#8E8E93")
                            font.pixelSize: 19
                            font.weight: Font.DemiBold
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }

                        background: Rectangle {
                            radius: 999
                            color: leftPageButton.hovered && leftPageButton.enabled ? (theme ? theme.bgSubtle : "#FBFBFD") : "transparent"

                            Behavior on color {
                                ColorAnimation {
                                    duration: motion ? motion.stateMs : 160
                                    easing.type: Easing.OutCubic
                                }
                            }
                        }
                    }

                    Rectangle {
                        width: 1
                        height: 18
                        anchors.verticalCenter: parent.verticalCenter
                        color: theme ? theme.lineSoft : "#E5E5EA"
                    }

                    Row {
                        height: 32
                        spacing: 4

                        TextField {
                            id: pageField
                            width: 40
                            height: 28
                            anchors.verticalCenter: parent.verticalCenter
                            text: root.currentPageNumber > 0 ? root.currentPageNumber.toString() : ""
                            enabled: root.totalPages > 0
                            horizontalAlignment: TextInput.AlignHCenter
                            verticalAlignment: TextInput.AlignVCenter
                            selectByMouse: true
                            color: theme ? theme.textPrimary : "#1D1D1F"
                            selectedTextColor: theme ? theme.textPrimary : "#1D1D1F"
                            selectionColor: theme ? theme.accentSoft : "#EAF3FF"
                            font.pixelSize: 13
                            font.weight: Font.DemiBold
                            validator: IntValidator {
                                bottom: 1
                                top: Math.max(1, root.totalPages)
                            }
                            ToolTip.text: root.pageJumpText
                            ToolTip.visible: hovered && root.pageJumpText.length > 0
                            onAccepted: root.pageJumpRequested(text)

                            background: Rectangle {
                                radius: 10
                                color: pageField.activeFocus ? (theme ? theme.bgSubtle : "#FBFBFD") : "transparent"
                                border.color: pageField.activeFocus ? (theme ? theme.accentPrimary : "#0071E3") : "transparent"
                                border.width: 1

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

                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: "/ " + Math.max(0, root.totalPages)
                            color: theme ? theme.textSecondary : "#6E6E73"
                            font.pixelSize: 13
                            font.weight: Font.DemiBold
                        }
                    }

                    Rectangle {
                        width: 1
                        height: 18
                        anchors.verticalCenter: parent.verticalCenter
                        color: theme ? theme.lineSoft : "#E5E5EA"
                    }

                    Button {
                        id: rightPageButton
                        width: 32
                        height: 30
                        enabled: root.canGoRight
                        text: "›"
                        scale: down ? 0.94 : 1.0
                        ToolTip.text: root.rightActionText
                        ToolTip.visible: hovered && root.rightActionText.length > 0
                        onClicked: root.rightRequested()

                        Behavior on scale {
                            NumberAnimation {
                                duration: motion ? motion.pressMs : 110
                                easing.type: Easing.OutCubic
                            }
                        }

                        contentItem: Text {
                            text: rightPageButton.text
                            color: rightPageButton.enabled ? (theme ? theme.textPrimary : "#1D1D1F") : (theme ? theme.textTertiary : "#8E8E93")
                            font.pixelSize: 19
                            font.weight: Font.DemiBold
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }

                        background: Rectangle {
                            radius: 999
                            color: rightPageButton.hovered && rightPageButton.enabled ? (theme ? theme.bgSubtle : "#FBFBFD") : "transparent"

                            Behavior on color {
                                ColorAnimation {
                                    duration: motion ? motion.stateMs : 160
                                    easing.type: Easing.OutCubic
                                }
                            }
                        }
                    }
                }
            }

            CheckBox {
                id: cropCheck
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                checked: root.showCropBoxes
                text: root.cropBoxText
                spacing: 7
                onClicked: root.cropBoxesToggled()

                indicator: Rectangle {
                    implicitWidth: 16
                    implicitHeight: 16
                    x: cropCheck.leftPadding
                    y: parent.height / 2 - height / 2
                    radius: 5
                    color: cropCheck.checked ? (theme ? theme.accentPrimary : "#0071E3") : (theme ? theme.surfaceBase : "#FFFFFF")
                    border.color: cropCheck.checked ? (theme ? theme.accentPrimary : "#0071E3") : (theme ? theme.lineSubtle : "#D2D2D7")
                    border.width: 1

                    Text {
                        anchors.centerIn: parent
                        text: "✓"
                        visible: cropCheck.checked
                        color: "#FFFFFF"
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                    }
                }

                contentItem: Text {
                    text: cropCheck.text
                    leftPadding: cropCheck.indicator.width + cropCheck.spacing
                    color: theme ? theme.textSecondary : "#6E6E73"
                    font.pixelSize: 13
                    font.weight: Font.Medium
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }
    }
}
