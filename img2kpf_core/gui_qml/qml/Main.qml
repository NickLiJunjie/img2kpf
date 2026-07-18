import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "theme"
import "components"
import "views"

ApplicationWindow {
    id: window
    visible: true
    width: 1240
    height: 780
    minimumWidth: 1080
    minimumHeight: 700
    title: "img2kpf Studio"
    color: theme.bgBase

    Theme {
        id: theme
        dark: window.value("themeMode", "light") === "dark"
    }
    Motion { id: motion }
    property var controller: appController
    property string languageToken: controller !== null && controller !== undefined ? controller.language : "zh"
    property bool inputSectionExpanded: true
    property bool toolsSectionExpanded: false
    property bool commonSectionExpanded: true
    property bool advancedExpanded: false
    property bool profilesSectionExpanded: false
    property var pendingRevealSection: null
    property int inspectorLabelWidth: 84
    property int controllerRevision: 0

    Component.onCompleted: controller = appController

    Connections {
        target: window.controller

        function onStateChanged() {
            window.controllerRevision += 1
        }

        function onDetectionChanged() {
            window.controllerRevision += 1
        }
    }

    function hasController() {
        return controller !== null && controller !== undefined
    }

    function uiText(key) {
        const token = languageToken + controllerRevision
        return hasController() ? controller.uiText(key) : key
    }

    function value(name, fallbackValue) {
        const token = controllerRevision
        return hasController() ? controller.valueForKey(name, fallbackValue) : fallbackValue
    }

    function canOpenPathLocation(kind) {
        const token = controllerRevision
        return hasController() ? controller.canOpenPathLocation(kind) : false
    }

    function runAccentColor(state) {
        if (state === "completed")
            return theme.successPrimary
        if (state === "partial" || state === "paused" || state === "pausing" || state === "cancelling" || state === "cancelled" || state === "stale")
            return theme.warningPrimary
        if (state === "failed")
            return theme.dangerBase
        if (state === "running")
            return theme.accentPrimary
        return state === "ready" ? theme.accentPrimary : theme.textSecondary
    }

    function runSoftColor(state) {
        if (state === "completed")
            return theme.successSoft
        if (state === "partial" || state === "paused" || state === "pausing" || state === "cancelling" || state === "cancelled" || state === "stale")
            return theme.warningSoft
        if (state === "failed")
            return theme.dangerSoft
        if (state === "running" || state === "ready")
            return theme.accentSoft
        return theme.bgSubtle
    }

    function logAccentColor(level) {
        if (level === "danger")
            return theme.dangerBase
        if (level === "success")
            return theme.successPrimary
        if (level === "warning")
            return theme.warningPrimary
        return theme.textTertiary
    }

    function logTextColor(level) {
        if (level === "danger")
            return theme.dangerBase
        if (level === "success")
            return theme.successPrimary
        if (level === "warning")
            return theme.warningPrimary
        return theme.textSecondary
    }

    function saveProfileFromField() {
        const name = profileNameField.text.trim()
        if (name.length === 0) {
            profileNameField.forceActiveFocus()
            return
        }
        if (controller.profileExists(name)) {
            overwriteProfileDialog.pendingProfileName = name
            overwriteProfileDialog.open()
            return
        }
        controller.saveProfile(name)
    }

    function openLoadProfileDialog() {
        const name = window.value("selectedProfileName", "")
        if (name.length === 0)
            return
        loadProfileDialog.pendingProfileName = name
        loadProfileDialog.open()
    }

    function pathLeaf(pathValue) {
        const normalized = String(pathValue || "").replace(/[\/\\]+$/, "")
        if (normalized.length === 0)
            return ""

        const segments = normalized.split(/[\/\\]/)
        return segments.length > 0 ? segments[segments.length - 1] : normalized
    }

    function focusConsumesPreviewArrows() {
        let item = activeFocusItem
        while (item) {
            if (item.previewArrowShortcutBlocker === true)
                return true

            const typeName = String(item)
            if (typeName.indexOf("TextInput") !== -1 || typeName.indexOf("TextField") !== -1 || typeName.indexOf("ComboBox") !== -1 || typeName.indexOf("SpinBox") !== -1)
                return true

            item = item.parent
        }
        return false
    }

    function previewArrowShortcutsEnabled() {
        return window.value("previewImageSource", "").length > 0 && !focusConsumesPreviewArrows()
    }

    Shortcut {
        sequence: "Left"
        context: Qt.WindowShortcut
        enabled: window.previewArrowShortcutsEnabled() && window.value("previewCanGoLeft", false)
        onActivated: controller.leftPreviewPage()
    }

    Shortcut {
        sequence: "Right"
        context: Qt.WindowShortcut
        enabled: window.previewArrowShortcutsEnabled() && window.value("previewCanGoRight", false)
        onActivated: controller.rightPreviewPage()
    }

    function revealSection(sectionItem) {
        if (!sectionItem || !inspectorScroll.contentItem)
            return

        const flickable = inspectorScroll.contentItem
        const margin = theme.space12
        const top = sectionItem.mapToItem(flickable, 0, 0).y
        const bottom = top + sectionItem.implicitHeight
        const viewTop = flickable.contentY
        const viewBottom = viewTop + flickable.height
        const maxY = Math.max(0, flickable.contentHeight - flickable.height)
        let targetY = flickable.contentY

        if (top < viewTop + margin)
            targetY = top - margin
        else if (bottom > viewBottom - margin)
            targetY = bottom - flickable.height + margin
        else
            return

        targetY = Math.max(0, Math.min(maxY, targetY))
        if (Math.abs(targetY - flickable.contentY) < 1)
            return

        scrollAnimation.stop()
        scrollAnimation.target = flickable
        scrollAnimation.from = flickable.contentY
        scrollAnimation.to = targetY
        scrollAnimation.restart()
    }

    function revealSectionAfterExpand(sectionItem) {
        pendingRevealSection = sectionItem
        revealSectionTimer.restart()
    }

    NumberAnimation {
        id: scrollAnimation
        property: "contentY"
        duration: motion.stateMs
        easing.type: Easing.OutCubic
    }

    Timer {
        id: revealSectionTimer
        interval: motion.sheetMs + 40
        repeat: false
        onTriggered: window.revealSection(window.pendingRevealSection)
    }

    FolderDialog {
        id: inputDialog
        title: window.uiText("ui.choose.input.folder")
        currentFolder: window.value("inputDialogFolder", "")
        onAccepted: controller.setInputDir(selectedFolder.toString())
    }

    FileDialog {
        id: templateDialog
        title: window.uiText("ui.choose.template.file")
        currentFolder: window.value("templateDialogFolder", "")
        nameFilters: ["Kindle Package (*.kpf *.zip)", "All Files (*)"]
        onAccepted: controller.setTemplatePath(selectedFile.toString())
    }

    FileDialog {
        id: kfxPluginDialog
        title: window.uiText("ui.kfx.plugin")
        currentFolder: window.value("kfxPluginDialogFolder", "")
        nameFilters: ["Zip Archive (*.zip)", "All Files (*)"]
        onAccepted: controller.importKfxPlugin(selectedFile.toString())
    }

    FolderDialog {
        id: outputFolderDialog
        title: window.uiText("ui.choose.output.directory")
        currentFolder: window.value("outputLocationDialogFolder", "")
        onAccepted: controller.setOutputLocation(selectedFolder.toString())
    }

    FileDialog {
        id: outputFileDialog
        title: window.uiText("ui.choose.output.file")
        fileMode: FileDialog.SaveFile
        currentFolder: window.value("outputLocationDialogFolder", "")
        nameFilters: [window.value("outputLocationFileFilter", "Kindle Package (*.kpf)")]
        onAccepted: controller.setOutputLocation(selectedFile.toString())
    }

    Dialog {
        id: overwriteProfileDialog
        property string pendingProfileName: ""
        title: window.uiText("ui.overwrite.existing.profile")
        modal: true
        standardButtons: Dialog.Ok | Dialog.Cancel
        width: 360
        x: Math.round((window.width - width) / 2)
        y: Math.round((window.height - implicitHeight) / 2)
        onAccepted: controller.saveProfile(pendingProfileName)

        contentItem: Text {
            width: 312
            text: window.uiText("ui.profile.already.exists.overwrite.it").replace("{name}", overwriteProfileDialog.pendingProfileName)
            color: theme.textPrimary
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }

        background: Rectangle {
            radius: theme.radiusCard
            color: theme.surfaceBase
            border.color: theme.lineSoft
            border.width: 1
        }
    }

    Dialog {
        id: deleteProfileDialog
        property string pendingProfileName: ""
        title: window.uiText("ui.profile.delete.title")
        modal: true
        standardButtons: Dialog.Ok | Dialog.Cancel
        width: 360
        x: Math.round((window.width - width) / 2)
        y: Math.round((window.height - implicitHeight) / 2)
        onAccepted: controller.deleteSelectedProfile()

        contentItem: Text {
            width: 312
            text: window.uiText("ui.delete.profile.generated.files.not.affected").replace("{name}", deleteProfileDialog.pendingProfileName)
            color: theme.textPrimary
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }

        background: Rectangle {
            radius: theme.radiusCard
            color: theme.surfaceBase
            border.color: theme.lineSoft
            border.width: 1
        }
    }

    Dialog {
        id: loadProfileDialog
        property string pendingProfileName: ""
        modal: true
        focus: true
        dim: true
        padding: 0
        standardButtons: Dialog.NoButton
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        width: Math.min(472, window.width - theme.space32 * 2)
        x: Math.round((window.width - width) / 2)
        y: Math.round((window.height - implicitHeight) / 2)

        Overlay.modal: Rectangle {
            color: theme.dark ? "#99000000" : "#521D1D1F"
        }

        enter: Transition {
            ParallelAnimation {
                NumberAnimation {
                    property: "opacity"
                    from: 0
                    to: 1
                    duration: motion.sheetMs
                    easing.type: Easing.OutCubic
                }
                NumberAnimation {
                    property: "scale"
                    from: 0.97
                    to: 1
                    duration: motion.sheetMs
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
                    duration: motion.stateMs
                    easing.type: Easing.OutCubic
                }
                NumberAnimation {
                    property: "scale"
                    from: 1
                    to: 0.98
                    duration: motion.stateMs
                    easing.type: Easing.OutCubic
                }
            }
        }

        contentItem: ColumnLayout {
            width: loadProfileDialog.width
            spacing: 0

            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 140

                Rectangle {
                    anchors.fill: parent
                    anchors.margins: theme.space16
                    radius: theme.radiusStage
                    color: theme.accentSoft
                    opacity: theme.dark ? 0.72 : 1
                }

                Rectangle {
                    width: 58
                    height: 58
                    radius: 18
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.top: parent.top
                    anchors.topMargin: theme.space24
                    color: theme.surfaceBase
                    border.color: theme.lineSoft
                    border.width: 1

                    Image {
                        anchors.centerIn: parent
                        width: 26
                        height: 26
                        sourceSize.width: 26
                        sourceSize.height: 26
                        source: Qt.resolvedUrl("../../assets/gui/icons/load_profile.svg")
                    }
                }

                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.top: parent.top
                    anchors.topMargin: 92
                    text: window.uiText("ui.profile.load.confirm.title")
                    color: theme.textPrimary
                    font.pixelSize: 18
                    font.weight: Font.DemiBold
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.leftMargin: theme.space24
                Layout.rightMargin: theme.space24
                Layout.bottomMargin: theme.space24
                spacing: theme.space12

                Rectangle {
                    Layout.alignment: Qt.AlignHCenter
                    implicitWidth: Math.min(profileNameText.implicitWidth + 28, loadProfileDialog.width - theme.space24 * 2)
                    implicitHeight: 32
                    radius: 12
                    color: theme.bgSubtle
                    border.color: theme.lineSoft
                    border.width: 1

                    Text {
                        id: profileNameText
                        anchors.fill: parent
                        anchors.leftMargin: 14
                        anchors.rightMargin: 14
                        text: loadProfileDialog.pendingProfileName
                        color: theme.textPrimary
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        maximumLineCount: 1
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: window.uiText("ui.profile.load.confirm.subtitle")
                    color: theme.textSecondary
                    font.pixelSize: 13
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: theme.space8

                    Text {
                        Layout.fillWidth: true
                        text: window.value("selectedProfileChangePreview", []).length > 0 ? window.uiText("ui.profile.will.update") : window.uiText("ui.profile.no.changes")
                        color: theme.textPrimary
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                    }

                    Repeater {
                        model: window.value("selectedProfileChangePreview", [])

                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true
                            implicitHeight: Math.max(44, profileChangeRow.implicitHeight + 18)
                            radius: 14
                            color: theme.bgSubtle
                            border.color: theme.lineSoft
                            border.width: 1

                            RowLayout {
                                id: profileChangeRow
                                anchors.fill: parent
                                anchors.leftMargin: theme.space12
                                anchors.rightMargin: theme.space12
                                spacing: theme.space8

                                Text {
                                    Layout.preferredWidth: 84
                                    text: modelData.label
                                    color: theme.textPrimary
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                    verticalAlignment: Text.AlignVCenter
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.current
                                    color: theme.textTertiary
                                    font.pixelSize: 12
                                    elide: Text.ElideRight
                                    horizontalAlignment: Text.AlignRight
                                    verticalAlignment: Text.AlignVCenter
                                }

                                Text {
                                    text: "→"
                                    color: theme.accentPrimary
                                    font.pixelSize: 13
                                    font.weight: Font.DemiBold
                                    verticalAlignment: Text.AlignVCenter
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.target
                                    color: theme.textPrimary
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: preserveProfileText.implicitHeight + 18
                    radius: 14
                    color: theme.dark ? "#222832" : "#F7FAFD"
                    border.color: theme.lineSoft
                    border.width: 1

                    Text {
                        id: preserveProfileText
                        anchors.fill: parent
                        anchors.leftMargin: theme.space12
                        anchors.rightMargin: theme.space12
                        verticalAlignment: Text.AlignVCenter
                        text: window.uiText("ui.profile.load.preserved.note")
                        color: theme.textSecondary
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: theme.space8

                    PrimaryButton {
                        Layout.fillWidth: true
                        theme: theme
                        motion: motion
                        prominent: false
                        text: window.uiText("ui.action.cancel")
                        onClicked: loadProfileDialog.close()
                    }

                    PrimaryButton {
                        Layout.fillWidth: true
                        theme: theme
                        motion: motion
                        text: window.uiText("ui.load.profile")
                        onClicked: {
                            controller.loadSelectedProfile()
                            loadProfileDialog.close()
                        }
                    }
                }
            }
        }

        background: Rectangle {
            radius: theme.radiusStage
            color: theme.surfaceBase
            border.color: theme.lineSoft
            border.width: 1
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: theme.space24
        spacing: theme.space16

        SquircleCard {
            Layout.fillWidth: true
            Layout.preferredHeight: 104
            theme: theme
            cardRadius: theme.radiusStage

            RowLayout {
                anchors.fill: parent
                anchors.margins: theme.space24
                spacing: theme.space16

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Text {
                        text: "img2kpf Studio"
                        color: theme.textPrimary
                        font.pixelSize: 24
                        font.weight: Font.DemiBold
                    }

                    RowLayout {
                        id: headerSummaryRow
                        Layout.fillWidth: true
                        visible: window.value("inputModeText", "").length > 0 || window.value("sourceSummary", "").length > 0
                        spacing: theme.space8

                        Rectangle {
                            visible: window.value("inputModeText", "").length > 0
                            implicitHeight: 28
                            implicitWidth: modeText.implicitWidth + 20
                            radius: 14
                            color: window.value("inputMode", "") === "batch" ? theme.accentSoft : theme.bgSubtle
                            border.color: window.value("inputMode", "") === "batch" ? theme.accentPrimary : theme.lineSubtle
                            border.width: 1

                            Text {
                                id: modeText
                                anchors.centerIn: parent
                                text: window.value("inputModeText", "")
                                color: window.value("inputMode", "") === "batch" ? theme.accentPrimary : theme.textPrimary
                                font.pixelSize: 12
                                font.weight: Font.DemiBold
                            }
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            visible: window.value("sourceSummary", "").length > 0
                            implicitHeight: 28
                            radius: 14
                            color: theme.bgSubtle
                            border.color: theme.lineSoft
                            border.width: 1

                            Text {
                                anchors.fill: parent
                                anchors.leftMargin: 12
                                anchors.rightMargin: 12
                                text: window.value("sourceSummary", "")
                                color: theme.textSecondary
                                font.pixelSize: 12
                                font.weight: Font.Medium
                                verticalAlignment: Text.AlignVCenter
                                elide: Text.ElideRight
                            }

                            HoverHandler {
                                id: headerSummaryHover
                                cursorShape: window.value("headerDetailText", "").length > 0 ? Qt.PointingHandCursor : Qt.ArrowCursor
                            }

                            ToolTip.visible: headerSummaryHover.hovered && window.value("headerDetailText", "").length > 0
                            ToolTip.delay: 420
                            ToolTip.text: window.value("headerDetailText", "")
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: !headerSummaryRow.visible && text.length > 0
                        text: window.value("headerCaptionText", "")
                        color: theme.textSecondary
                        font.pixelSize: 13
                        wrapMode: Text.WordWrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                    }
                }

                Rectangle {
                    readonly property string runState: window.value("runState", "setup")
                    radius: 999
                    color: window.runSoftColor(runState)
                    border.color: window.runAccentColor(runState)
                    border.width: 1
                    implicitWidth: badgeText.width + 30
                    implicitHeight: 34

                    Behavior on color {
                        ColorAnimation {
                            duration: motion.stateMs
                            easing.type: Easing.OutCubic
                        }
                    }

                    Behavior on border.color {
                        ColorAnimation {
                            duration: motion.stateMs
                            easing.type: Easing.OutCubic
                        }
                    }

                    Text {
                        id: badgeText
                        anchors.centerIn: parent
                        text: window.value("runStateText", window.uiText("ui.setup"))
                        color: window.runAccentColor(parent.runState)
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                    }
                }

                RowLayout {
                    spacing: theme.space8

                    IconButton {
                        id: themeButton
                        theme: theme
                        motion: motion
                        symbol: theme.dark ? "☾" : "☀"
                        toggled: theme.dark
                        checked: theme.dark
                        toolTipText: theme.dark ? window.uiText("ui.theme.switch.light") : window.uiText("ui.theme.switch.dark")
                        onClicked: controller.toggleThemeMode()
                    }

                    IconButton {
                        id: languageButton
                        theme: theme
                        motion: motion
                        iconSource: Qt.resolvedUrl("../../assets/gui/language_globe.svg")
                        checked: languagePopup.visible
                        toolTipText: window.uiText("ui.language.label")
                        onClicked: languagePopup.open()
                    }

                    Popup {
                        id: languagePopup
                        x: languageButton.x + languageButton.width - width
                        y: languageButton.y + languageButton.height + 8
                        width: 164
                        padding: 6
                        modal: false
                        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
                        opacity: 0
                        scale: 0.98
                        transformOrigin: Popup.TopRight

                        contentItem: Column {
                            spacing: 2
                            Repeater {
                                model: window.value("languageOptions", [])

                                ItemDelegate {
                                    id: languageItem
                                    required property var modelData
                                    width: languagePopup.width - languagePopup.leftPadding - languagePopup.rightPadding
                                    height: 34
                                    onClicked: {
                                        controller.setLanguage(modelData.value)
                                        languagePopup.close()
                                    }

                                    contentItem: RowLayout {
                                        spacing: 8
                                        Text {
                                            Layout.fillWidth: true
                                            text: languageItem.modelData.label
                                            color: window.value("language", "zh") === languageItem.modelData.value ? theme.accentPrimary : theme.textPrimary
                                            font.pixelSize: 13
                                            font.weight: window.value("language", "zh") === languageItem.modelData.value ? Font.DemiBold : Font.Normal
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                        Text {
                                            text: "●"
                                            visible: window.value("language", "zh") === languageItem.modelData.value
                                            color: theme.accentPrimary
                                            font.pixelSize: 9
                                        }
                                    }

                                    background: Rectangle {
                                        radius: theme.radiusControl
                                        color: languageItem.hovered || window.value("language", "zh") === languageItem.modelData.value ? theme.accentSoft : theme.surfaceBase
                                    }
                                }
                            }
                        }

                        background: Rectangle {
                            radius: theme.radiusCard
                            color: theme.surfaceBase
                            border.color: theme.lineSoft
                            border.width: 1
                        }

                        enter: Transition {
                            ParallelAnimation {
                                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: motion.panelMs; easing.type: Easing.OutCubic }
                                NumberAnimation { property: "scale"; from: 0.98; to: 1; duration: motion.panelMs; easing.type: Easing.OutQuint }
                            }
                        }

                        exit: Transition {
                            ParallelAnimation {
                                NumberAnimation { property: "opacity"; from: 1; to: 0; duration: motion.stateMs; easing.type: Easing.OutCubic }
                                NumberAnimation { property: "scale"; from: 1; to: 0.98; duration: motion.stateMs; easing.type: Easing.OutCubic }
                            }
                        }

                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: theme.space16

            SquircleCard {
                Layout.preferredWidth: 390
                Layout.fillHeight: true
                theme: theme

                ScrollView {
                    id: inspectorScroll
                    anchors.fill: parent
                    anchors.margins: theme.space24
                    clip: true
                    rightPadding: 14
                    contentWidth: availableWidth

                    ScrollBar.vertical: ScrollBar {
                        policy: ScrollBar.AsNeeded
                        width: 6
                        opacity: active ? 0.55 : 0.28
                    }

                    ColumnLayout {
                        width: inspectorScroll.availableWidth
                        spacing: theme.space16

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: theme.space8

                            Text {
                                Layout.fillWidth: true
                                text: window.uiText("ui.settings")
                                color: theme.textPrimary
                                font.pixelSize: 18
                                font.weight: Font.DemiBold
                                verticalAlignment: Text.AlignVCenter
                            }

                            IconButton {
                                theme: theme
                                motion: motion
                                buttonSize: 34
                                iconSize: 18
                                iconSource: Qt.resolvedUrl("../../assets/gui/icons/refresh.svg")
                                toolTipText: window.uiText("ui.action.refresh")
                                onClicked: controller.refreshCurrentState()
                            }

                            IconButton {
                                theme: theme
                                motion: motion
                                buttonSize: 34
                                iconSize: 18
                                iconSource: Qt.resolvedUrl("../../assets/gui/icons/reset.svg")
                                toolTipText: window.uiText("ui.action.reset.defaults")
                                onClicked: controller.resetSettingsToDefaults()
                            }
                        }

                        CollapsibleSection {
                            id: inputSection
                            theme: theme
                            motion: motion
                            title: window.uiText("ui.input.output")
                            expanded: window.inputSectionExpanded
                            actionText: window.uiText("ui.action.clear")
                            actionIconSource: Qt.resolvedUrl("../../assets/gui/icons/clear.svg")
                            actionEnabled: window.value("canClearInputOutput", false)
                            contentSpacing: theme.space8
                            onToggleRequested: expanded => {
                                window.inputSectionExpanded = expanded
                            }
                            onActionRequested: controller.clearInputOutput()

                            FieldCard {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                label: window.uiText("ui.input.folder")
                                metaText: window.pathLeaf(window.value("inputDir", ""))

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: theme.space12

                                    TextEntry {
                                        Layout.fillWidth: true
                                        theme: theme
                                        motion: motion
                                        text: window.value("inputDir", "")
                                        revealEnd: false
                                        placeholderText: window.uiText("ui.input.folder")
                                        onEditingFinished: controller.setInputDir(text)
                                    }

                                    IconButton {
                                        theme: theme
                                        motion: motion
                                        buttonSize: 34
                                        iconSize: 18
                                        iconSource: Qt.resolvedUrl("../../assets/gui/icons/folder.svg")
                                        toolTipText: window.uiText("ui.action.choose.input.folder")
                                        onClicked: inputDialog.open()
                                    }

                                    IconButton {
                                        theme: theme
                                        motion: motion
                                        buttonSize: 34
                                        iconSize: 18
                                        iconSource: Qt.resolvedUrl("../../assets/gui/icons/open_output.svg")
                                        toolTipText: window.uiText("ui.action.open.input.folder")
                                        enabled: window.canOpenPathLocation("input")
                                        onClicked: controller.openPathLocation("input")
                                    }
                                }
                            }

                            FieldCard {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                label: window.uiText("ui.status.output_location")
                                metaText: window.pathLeaf(window.value("outputLocation", ""))

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: theme.space12

                                    TextEntry {
                                        Layout.fillWidth: true
                                        theme: theme
                                        motion: motion
                                        text: window.value("outputLocation", "")
                                        revealEnd: false
                                        placeholderText: window.uiText("ui.status.output_location")
                                        onEditingFinished: controller.setOutputLocation(text)
                                    }

                                    IconButton {
                                        theme: theme
                                        motion: motion
                                        buttonSize: 34
                                        iconSize: 18
                                        iconSource: Qt.resolvedUrl("../../assets/gui/icons/folder.svg")
                                        toolTipText: window.value("outputLocationPickerMode", "file") === "folder" ? window.uiText("ui.choose.output.directory") : window.uiText("ui.choose.output.file")
                                        onClicked: {
                                            if (window.value("outputLocationPickerMode", "file") === "folder")
                                                outputFolderDialog.open()
                                            else
                                                outputFileDialog.open()
                                        }
                                    }

                                    IconButton {
                                        theme: theme
                                        motion: motion
                                        buttonSize: 34
                                        iconSize: 18
                                        iconSource: Qt.resolvedUrl("../../assets/gui/icons/open_output.svg")
                                        toolTipText: window.uiText("ui.action.open.output.location")
                                        enabled: window.canOpenPathLocation("output")
                                        onClicked: controller.openPathLocation("output")
                                    }
                                }
                            }

                            FieldCard {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                label: window.uiText("ui.title.settings")
                                metaText: window.value("titleEffectSummary", "")

                                CheckBox {
                                    id: customTitleCheck
                                    Layout.fillWidth: true
                                    text: window.uiText("ui.custom.title")
                                    checked: window.value("customTitleEnabled", false)
                                    spacing: 8
                                    onToggled: controller.setCustomTitleEnabled(checked)

                                    indicator: Rectangle {
                                        x: customTitleCheck.leftPadding
                                        y: parent.height / 2 - height / 2
                                        width: 18
                                        height: 18
                                        radius: 5
                                        color: customTitleCheck.checked ? theme.accentPrimary : theme.surfaceBase
                                        border.color: customTitleCheck.checked ? theme.accentPrimary : theme.lineSubtle
                                        border.width: 1

                                        Text {
                                            anchors.centerIn: parent
                                            visible: customTitleCheck.checked
                                            text: "✓"
                                            color: "#FFFFFF"
                                            font.pixelSize: 13
                                            font.weight: Font.DemiBold
                                        }
                                    }

                                    contentItem: Text {
                                        text: customTitleCheck.text
                                        color: theme.textPrimary
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                        verticalAlignment: Text.AlignVCenter
                                        leftPadding: customTitleCheck.indicator.width + customTitleCheck.spacing
                                    }
                                }

                                TextEntry {
                                    Layout.fillWidth: true
                                    visible: window.value("customTitleEnabled", false)
                                    theme: theme
                                    motion: motion
                                    text: window.value("title", "")
                                    placeholderText: window.value("inputMode", "") === "batch" ? window.uiText("ui.series.name") : window.uiText("ui.title")
                                    onTextEdited: controller.setTitle(text)
                                    onEditingFinished: controller.setTitle(text)
                                }

                                TextEntry {
                                    Layout.fillWidth: true
                                    visible: window.value("volumeTitleTemplateVisible", false)
                                    theme: theme
                                    motion: motion
                                    text: window.value("volumeTitleTemplate", "")
                                    placeholderText: window.uiText("ui.volume.title.template")
                                    onTextEdited: controller.setVolumeTitleTemplate(text)
                                    onEditingFinished: controller.setVolumeTitleTemplate(text)
                                }
                            }

                            FieldCard {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                label: window.uiText("ui.cover.settings")
                                metaText: window.value("coverEffectSummary", "")

                                OptionRow {
                                    Layout.fillWidth: true
                                    theme: theme
                                    motion: motion
                                    labelWidth: window.inspectorLabelWidth
                                    label: window.uiText("ui.cover.source")
                                    options: window.value("coverModeOptions", [])
                                    value: window.value("coverMode", "auto")
                                    onSelected: value => controller.setCoverMode(value)
                                }

                                OptionRow {
                                    Layout.fillWidth: true
                                    visible: window.value("coverBatchScopeVisible", false)
                                    theme: theme
                                    motion: motion
                                    labelWidth: window.inspectorLabelWidth
                                    label: window.uiText("ui.cover.batch.scope")
                                    options: window.value("coverBatchScopeOptions", [])
                                    value: window.value("coverBatchScope", "uniform")
                                    onSelected: value => controller.setCoverBatchScope(value)
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    visible: window.value("coverMode", "auto") === "page"
                                    spacing: theme.space8

                                    Text {
                                        Layout.preferredWidth: window.inspectorLabelWidth
                                        text: window.value("coverPerVolumeVisible", false) ? window.uiText("ui.cover.current.volume.page") : window.uiText("ui.cover.page")
                                        color: theme.textSecondary
                                        font.pixelSize: 13
                                        font.weight: Font.Medium
                                        horizontalAlignment: Text.AlignRight
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                    }

                                    TextEntry {
                                        id: coverPageField
                                        Layout.fillWidth: true
                                        theme: theme
                                        motion: motion
                                        text: window.value("coverPerVolumeVisible", false) ? String(window.value("currentVolumeCoverPageNumber", 1)) : String(window.value("coverPageNumber", 1))
                                        inputMethodHints: Qt.ImhDigitsOnly
                                        horizontalAlignment: TextInput.AlignHCenter
                                        validator: IntValidator {
                                            bottom: 1
                                            top: Math.max(1, window.value("previewTotalPages", 9999))
                                        }
                                        onEditingFinished: {
                                            if (window.value("coverPerVolumeVisible", false))
                                                controller.setCurrentVolumeCoverPageNumberText(text)
                                            else
                                                controller.setCoverPageNumberText(text)
                                        }
                                        onAccepted: {
                                            if (window.value("coverPerVolumeVisible", false))
                                                controller.setCurrentVolumeCoverPageNumberText(text)
                                            else
                                                controller.setCoverPageNumberText(text)
                                        }
                                    }

                                    PrimaryButton {
                                        theme: theme
                                        motion: motion
                                        prominent: false
                                        text: window.uiText("ui.cover.jump")
                                        enabled: window.value("previewTotalPages", 0) > 0
                                        onClicked: controller.jumpToCoverPage()
                                    }
                                }

                                CheckBox {
                                    id: coverWatermarkCheck
                                    Layout.fillWidth: true
                                    text: window.uiText("ui.cover.show.watermark")
                                    checked: window.value("coverWatermarkEnabled", true)
                                    spacing: 8
                                    onToggled: controller.setCoverWatermarkEnabled(checked)

                                    indicator: Rectangle {
                                        x: coverWatermarkCheck.leftPadding
                                        y: parent.height / 2 - height / 2
                                        width: 18
                                        height: 18
                                        radius: 5
                                        color: coverWatermarkCheck.checked ? theme.accentPrimary : theme.surfaceBase
                                        border.color: coverWatermarkCheck.checked ? theme.accentPrimary : theme.lineSubtle
                                        border.width: 1

                                        Text {
                                            anchors.centerIn: parent
                                            visible: coverWatermarkCheck.checked
                                            text: "✓"
                                            color: "#FFFFFF"
                                            font.pixelSize: 13
                                            font.weight: Font.DemiBold
                                        }
                                    }

                                    contentItem: Text {
                                        text: coverWatermarkCheck.text
                                        color: theme.textPrimary
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                        verticalAlignment: Text.AlignVCenter
                                        leftPadding: coverWatermarkCheck.indicator.width + coverWatermarkCheck.spacing
                                    }
                                }
                            }
                        }

                        CollapsibleSection {
                            id: commonSection
                            theme: theme
                            motion: motion
                            title: window.uiText("ui.common")
                            expanded: window.commonSectionExpanded
                            onToggleRequested: expanded => {
                                window.commonSectionExpanded = expanded
                            }

                            OptionRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                label: window.uiText("ui.image.style")
                                options: window.value("imageStyleOptions", [])
                                value: window.value("imageStyle", "")
                                onSelected: value => controller.setImageStyle(value)
                            }

                            OptionRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                label: window.uiText("ui.preserve.color")
                                options: window.value("preserveColorOptions", [])
                                value: window.value("preserveColor", "")
                                onSelected: value => controller.setOption("preserve_color", value)
                            }

                            Text {
                                visible: window.value("imageCustom", false)
                                text: window.value("imageCustomBaseText", "")
                                color: theme.textSecondary
                                font.pixelSize: 12
                                font.weight: Font.DemiBold
                                Layout.topMargin: 2
                            }

                            SliderRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                visible: window.value("imageCustom", false)
                                label: window.uiText("ui.gamma")
                                from: 0.5
                                to: 2.6
                                stepSize: 0.1
                                value: window.value("gammaValue", 1.8)
                                displayValue: value.toFixed(1)
                                onValueEdited: value => controller.setGammaValue(value)
                                onResetClicked: controller.resetGammaValue()
                            }

                            SliderRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                visible: window.value("imageCustom", false)
                                label: window.uiText("ui.contrast")
                                from: 0.6
                                to: 1.8
                                stepSize: 0.05
                                value: window.value("contrastValue", 1.0)
                                displayValue: value.toFixed(2)
                                onValueEdited: value => controller.setContrastValue(value)
                                onResetClicked: controller.resetContrastValue()
                            }

                            SliderRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                visible: window.value("imageCustom", false)
                                label: window.uiText("ui.jpeg.quality")
                                from: 60
                                to: 100
                                stepSize: 1
                                value: window.value("jpegQualityValue", 90)
                                displayValue: Math.round(value).toString()
                                onValueEdited: value => controller.setJpegQualityValue(Math.round(value))
                                onResetClicked: controller.resetJpegQualityValue()
                            }

                            OptionRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                label: window.uiText("ui.crop")
                                options: window.value("cropModeOptions", [])
                                value: window.value("cropMode", "")
                                onSelected: value => controller.setOption("crop_mode", value)
                            }

                            SliderRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                visible: window.value("cropEdgeThresholdEnabled", false)
                                label: window.uiText("ui.crop.edge.threshold")
                                from: 0.70
                                to: 1.00
                                stepSize: 0.01
                                value: window.value("cropEdgeThreshold", 1.00)
                                displayValue: value.toFixed(2)
                                onValueEdited: value => controller.setCropEdgeThreshold(value)
                                onResetClicked: controller.resetCropEdgeThreshold()
                            }

                            SliderRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                visible: false
                                label: window.uiText("ui.spread.outer.edge.threshold")
                                from: 0.70
                                to: 1.00
                                stepSize: 0.01
                                value: window.value("spreadFillEdgeThreshold", 0.90)
                                displayValue: value.toFixed(2)
                                onValueEdited: value => controller.setSpreadFillEdgeThreshold(value)
                                onResetClicked: controller.resetSpreadFillEdgeThreshold()
                            }

                            CheckBox {
                                id: spreadInnerCropCheck
                                Layout.fillWidth: true
                                visible: window.value("spreadFillEdgeThresholdEnabled", false)
                                text: window.uiText("ui.spread.inner.crop")
                                checked: window.value("spreadFillInnerEnabled", false)
                                spacing: 8
                                onToggled: controller.setSpreadFillInnerEnabled(checked)

                                indicator: Rectangle {
                                    x: spreadInnerCropCheck.leftPadding
                                    y: parent.height / 2 - height / 2
                                    width: 18
                                    height: 18
                                    radius: 5
                                    color: spreadInnerCropCheck.checked ? theme.accentPrimary : theme.surfaceBase
                                    border.color: spreadInnerCropCheck.checked ? theme.accentPrimary : theme.lineSubtle
                                    border.width: 1

                                    Text {
                                        anchors.centerIn: parent
                                        text: "✓"
                                        color: "#FFFFFF"
                                        font.pixelSize: 13
                                        font.weight: Font.Bold
                                        visible: spreadInnerCropCheck.checked
                                    }
                                }

                                contentItem: Text {
                                    text: spreadInnerCropCheck.text
                                    color: theme.textPrimary
                                    font.pixelSize: 14
                                    verticalAlignment: Text.AlignVCenter
                                    leftPadding: spreadInnerCropCheck.indicator.width + spreadInnerCropCheck.spacing
                                }
                            }

                            SliderRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                visible: false
                                label: window.uiText("ui.spread.inner.edge.threshold")
                                from: 0.70
                                to: 1.00
                                stepSize: 0.01
                                value: window.value("spreadFillInnerEdgeThreshold", 0.90)
                                displayValue: value.toFixed(2)
                                onValueEdited: value => controller.setSpreadFillInnerEdgeThreshold(value)
                                onResetClicked: controller.resetSpreadFillInnerEdgeThreshold()
                            }

                            OptionRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                label: window.uiText("ui.reading.direction")
                                options: window.value("readingDirectionOptions", [])
                                value: window.value("readingDirection", "")
                                onSelected: value => controller.setOption("reading_direction", value)
                            }

                            OptionRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                label: window.uiText("ui.layout")
                                options: window.value("pageLayoutOptions", [])
                                value: window.value("pageLayout", "")
                                onSelected: value => controller.setOption("page_layout", value)
                            }

                            OptionRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                visible: window.value("shiftModeEnabled", false)
                                label: window.uiText("ui.first.shift")
                                options: window.value("shiftModeOptions", [])
                                value: window.value("shiftMode", "")
                                onSelected: value => controller.setOption("shift_mode", value)
                            }

                            OptionRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                label: window.uiText("ui.virtual.panels")
                                options: window.value("virtualPanelsOptions", [])
                                value: window.value("virtualPanels", "")
                                onSelected: value => controller.setOption("virtual_panels", value)
                            }

                            OptionRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                visible: window.value("panelMovementEnabled", false)
                                label: window.uiText("ui.panel.movement")
                                options: window.value("panelMovementOptions", [])
                                value: window.value("panelMovement", "")
                                onSelected: value => controller.setOption("panel_movement", value)
                            }

                            OptionRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                label: window.uiText("ui.panel.size")
                                options: window.value("panelPresetOptions", [])
                                value: window.value("panelPreset", "")
                                onSelected: value => controller.setOption("panel_preset", value)
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                visible: window.value("panelPresetCustom", false)
                                spacing: theme.space12

                                    Text {
                                        text: window.uiText("ui.set.single.canvas.size")
                                        color: theme.textSecondary
                                        font.pixelSize: 13
                                        font.weight: Font.Medium
                                        Layout.preferredWidth: window.inspectorLabelWidth
                                        horizontalAlignment: Text.AlignRight
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                    }

                                TextEntry {
                                    Layout.fillWidth: true
                                    theme: theme
                                    motion: motion
                                    text: window.value("targetSizeText", "")
                                    placeholderText: "1240x1860"
                                    onEditingFinished: controller.setTargetSizeText(text)
                                }
                            }

                            OptionRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                label: window.uiText("ui.output.format")
                                options: window.value("outputFormatOptions", [])
                                value: window.value("outputFormat", "")
                                onSelected: value => controller.setOption("output_format", value)
                            }
                        }

                        CollapsibleSection {
                            id: toolsSection
                            theme: theme
                            motion: motion
                            title: window.uiText("ui.tools")
                            expanded: window.toolsSectionExpanded
                            onToggleRequested: expanded => {
                                window.toolsSectionExpanded = expanded
                            }

                            FieldCard {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                label: window.uiText("ui.spread.split.tool")
                                metaText: window.value("splitSpreadsSummary", "")

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: theme.space12

                                    Text {
                                        Layout.fillWidth: true
                                        text: window.value("splitSpreadsSummary", "")
                                        color: theme.textSecondary
                                        font.pixelSize: 12
                                        wrapMode: Text.WordWrap
                                        verticalAlignment: Text.AlignVCenter
                                    }

                                    Item {
                                        id: splitSpreadsButtonHost
                                        readonly property bool splitAvailable: window.value("canSplitSpreads", false)
                                        Layout.preferredWidth: 132
                                        Layout.preferredHeight: 36

                                        PrimaryButton {
                                            id: splitSpreadsButton
                                            anchors.fill: parent
                                            theme: theme
                                            motion: motion
                                            prominent: false
                                            text: window.uiText("ui.action.split.spreads")
                                            enabled: splitSpreadsButtonHost.splitAvailable
                                            onClicked: controller.splitSpreads()
                                        }

                                        MouseArea {
                                            id: splitDisabledHover
                                            anchors.fill: parent
                                            hoverEnabled: true
                                            acceptedButtons: Qt.NoButton
                                            visible: !splitSpreadsButtonHost.splitAvailable
                                            enabled: visible
                                            cursorShape: Qt.ForbiddenCursor
                                        }

                                        ToolTip.visible: splitDisabledHover.visible && splitDisabledHover.containsMouse
                                        ToolTip.delay: 260
                                        ToolTip.text: window.value("splitSpreadsToolTip", "")
                                    }
                                }
                            }
                        }

                        CollapsibleSection {
                            id: advancedSection
                            theme: theme
                            motion: motion
                            title: window.uiText("ui.advanced")
                            expanded: window.advancedExpanded
                            onToggleRequested: expanded => {
                                window.advancedExpanded = expanded
                            }

                            Item {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 44

                                RowLayout {
                                    anchors.fill: parent
                                    spacing: theme.space12

                                    Text {
                                        text: window.uiText("ui.kfx.plugin")
                                        color: theme.textSecondary
                                        font.pixelSize: 13
                                        font.weight: Font.Medium
                                        Layout.preferredWidth: window.inspectorLabelWidth
                                        horizontalAlignment: Text.AlignRight
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                    }

                                    Rectangle {
                                        Layout.preferredWidth: 88
                                        Layout.preferredHeight: 32
                                        radius: 16
                                        color: {
                                            const status = window.value("kfxPluginStatus", "missing")
                                            if (status === "ready")
                                                return theme.successSoft
                                            if (status === "external")
                                                return theme.accentSoft
                                            return theme.bgSubtle
                                        }
                                        border.color: {
                                            const status = window.value("kfxPluginStatus", "missing")
                                            if (status === "ready")
                                                return theme.successPrimary
                                            if (status === "external")
                                                return theme.accentPrimary
                                            return theme.lineSubtle
                                        }
                                        border.width: 1

                                        RowLayout {
                                            anchors.centerIn: parent
                                            spacing: 5

                                            Text {
                                                visible: window.value("kfxPluginStatus", "missing") !== "missing"
                                                text: "✓"
                                                color: window.value("kfxPluginStatus", "missing") === "ready" ? theme.successPrimary : theme.accentPrimary
                                                font.pixelSize: 12
                                                font.weight: Font.DemiBold
                                            }

                                            Text {
                                                text: window.value("kfxPluginStatusText", "")
                                                color: {
                                                    const status = window.value("kfxPluginStatus", "missing")
                                                    if (status === "ready")
                                                        return theme.successPrimary
                                                    if (status === "external")
                                                        return theme.accentPrimary
                                                    return theme.textTertiary
                                                }
                                                font.pixelSize: 12
                                                font.weight: Font.DemiBold
                                            }
                                        }

                                        MouseArea {
                                            id: kfxPluginStatusHover
                                            anchors.fill: parent
                                            hoverEnabled: true
                                            acceptedButtons: Qt.NoButton
                                        }

                                        ToolTip.visible: kfxPluginStatusHover.containsMouse
                                        ToolTip.delay: 420
                                        ToolTip.text: window.value("kfxPluginDisplayText", "")
                                    }

                                    IconButton {
                                        theme: theme
                                        motion: motion
                                        buttonSize: 36
                                        iconSize: 19
                                        iconSource: Qt.resolvedUrl("../../assets/gui/icons/folder.svg")
                                        toolTipText: window.uiText("ui.action.import.kfx.plugin")
                                        onClicked: kfxPluginDialog.open()
                                    }

                                    IconButton {
                                        theme: theme
                                        motion: motion
                                        buttonSize: 36
                                        iconSize: 19
                                        iconSource: Qt.resolvedUrl("../../assets/gui/icons/open_output.svg")
                                        toolTipText: window.uiText("ui.action.open.kfx.plugin.location")
                                        enabled: window.canOpenPathLocation("kfx")
                                        onClicked: controller.openPathLocation("kfx")
                                    }

                                    IconButton {
                                        theme: theme
                                        motion: motion
                                        buttonSize: 36
                                        iconSize: 18
                                        symbol: "×"
                                        toolTipText: window.uiText("ui.action.remove.kfx.plugin")
                                        enabled: window.value("kfxPluginImported", false)
                                        onClicked: controller.removeKfxPlugin()
                                    }
                                }

                                DropArea {
                                    id: kfxPluginDropArea
                                    anchors.fill: parent
                                    onDropped: drop => {
                                        if (drop.urls.length > 0) {
                                            controller.importKfxPlugin(drop.urls[0].toString())
                                        }
                                    }
                                }

                                Item {
                                    anchors.fill: parent
                                    visible: kfxPluginDropArea.containsDrag
                                    z: 10

                                    Rectangle {
                                        anchors.fill: parent
                                        radius: theme.radiusControl
                                        color: theme.dark ? Qt.rgba(0.08, 0.09, 0.11, 0.84) : Qt.rgba(1, 1, 1, 0.78)
                                    }

                                    Canvas {
                                        anchors.fill: parent
                                        onPaint: {
                                            const ctx = getContext("2d")
                                            const inset = 1
                                            const radius = theme.radiusControl
                                            const w = width - inset * 2
                                            const h = height - inset * 2
                                            ctx.clearRect(0, 0, width, height)
                                            ctx.strokeStyle = String(theme.accentPrimary)
                                            ctx.lineWidth = 1.5
                                            ctx.setLineDash([7, 5])
                                            ctx.beginPath()
                                            ctx.moveTo(inset + radius, inset)
                                            ctx.lineTo(inset + w - radius, inset)
                                            ctx.quadraticCurveTo(inset + w, inset, inset + w, inset + radius)
                                            ctx.lineTo(inset + w, inset + h - radius)
                                            ctx.quadraticCurveTo(inset + w, inset + h, inset + w - radius, inset + h)
                                            ctx.lineTo(inset + radius, inset + h)
                                            ctx.quadraticCurveTo(inset, inset + h, inset, inset + h - radius)
                                            ctx.lineTo(inset, inset + radius)
                                            ctx.quadraticCurveTo(inset, inset, inset + radius, inset)
                                            ctx.stroke()
                                        }
                                        onWidthChanged: requestPaint()
                                        onHeightChanged: requestPaint()
                                        Component.onCompleted: requestPaint()
                                    }

                                    RowLayout {
                                        anchors.centerIn: parent
                                        spacing: theme.space8

                                        Rectangle {
                                            Layout.preferredWidth: 24
                                            Layout.preferredHeight: 24
                                            radius: 12
                                            color: theme.accentPrimary

                                            Text {
                                                anchors.centerIn: parent
                                                text: "+"
                                                color: "#FFFFFF"
                                                font.pixelSize: 18
                                                font.weight: Font.DemiBold
                                            }
                                        }

                                        ColumnLayout {
                                            spacing: 0

                                            Text {
                                                text: window.uiText("ui.kfx.plugin.drop.active")
                                                color: theme.textPrimary
                                                font.pixelSize: 13
                                                font.weight: Font.DemiBold
                                            }

                                            Text {
                                                text: window.uiText("ui.kfx.plugin.drop.subtitle")
                                                color: theme.textTertiary
                                                font.pixelSize: 11
                                            }
                                        }
                                    }
                                }
                            }

                            OptionRow {
                                Layout.fillWidth: true
                                theme: theme
                                motion: motion
                                labelWidth: window.inspectorLabelWidth
                                label: window.uiText("ui.performance.mode")
                                options: window.value("performanceModeOptions", [])
                                value: window.value("performanceMode", "balanced")
                                onSelected: value => controller.setOption("performance_mode", value)
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                visible: false
                                spacing: 0

                                SliderRow {
                                    Layout.fillWidth: true
                                    theme: theme
                                    motion: motion
                                    labelWidth: window.inspectorLabelWidth
                                    label: window.uiText("ui.parallel.volumes")
                                    from: window.value("jobsMin", 1)
                                    to: window.value("jobsMax", 16)
                                    stepSize: 1
                                    value: window.value("jobs", window.value("jobsDefault", 5))
                                    displayValue: Math.round(value).toString()
                                    onValueEdited: value => controller.setJobs(Math.round(value))
                                    onResetClicked: controller.setJobs(window.value("jobsDefault", 5))
                                }
                            }
                        }

                        CollapsibleSection {
                            id: profilesSection
                            theme: theme
                            motion: motion
                            title: window.uiText("ui.profiles")
                            expanded: window.profilesSectionExpanded
                            contentSpacing: theme.space8
                            onToggleRequested: expanded => {
                                window.profilesSectionExpanded = expanded
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: theme.space8

                                TextEntry {
                                    id: profileNameField
                                    Layout.fillWidth: true
                                    theme: theme
                                    motion: motion
                                    text: window.value("selectedProfileName", "")
                                    placeholderText: window.uiText("ui.profile.name")
                                    onAccepted: window.saveProfileFromField()
                                }

                                IconButton {
                                    theme: theme
                                    motion: motion
                                    buttonSize: 36
                                    iconSize: 19
                                    iconSource: Qt.resolvedUrl("../../assets/gui/icons/save.svg")
                                    toolTipText: window.uiText("ui.save.settings.new.profile")
                                    enabled: profileNameField.text.trim().length > 0
                                    onClicked: window.saveProfileFromField()
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: theme.space8

                                ComboBox {
                                    id: profileCombo
                                    Layout.fillWidth: true
                                    implicitHeight: 36
                                    model: window.value("profileOptions", [])
                                    textRole: "label"
                                    valueRole: "value"
                                    font.pixelSize: 13

                                    function indexForValue(candidate) {
                                        const options = window.value("profileOptions", [])
                                        for (let index = 0; index < options.length; index += 1) {
                                            if (options[index].value === candidate)
                                                return index
                                        }
                                        return -1
                                    }

                                    Component.onCompleted: currentIndex = indexForValue(window.value("selectedProfileName", ""))
                                    onCurrentValueChanged: {
                                        if (!profileNameField.activeFocus)
                                            profileNameField.text = currentValue || ""
                                    }
                                    onActivated: {
                                        controller.setSelectedProfileName(currentValue)
                                        profileNameField.text = currentValue
                                    }

                                    Connections {
                                        target: window
                                        function onControllerRevisionChanged() {
                                            profileCombo.currentIndex = profileCombo.indexForValue(window.value("selectedProfileName", ""))
                                        }
                                    }

                                    contentItem: Text {
                                        leftPadding: 12
                                        rightPadding: 28
                                        text: profileCombo.displayText || window.uiText("ui.no.saved.profiles")
                                        color: theme.textPrimary
                                        font: profileCombo.font
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                    }

                                    ToolTip.text: {
                                        const options = window.value("profileOptions", [])
                                        const index = profileCombo.currentIndex
                                        return index >= 0 && index < options.length ? (options[index].tooltip || "") : window.uiText("ui.select.saved.profile")
                                    }
                                    ToolTip.visible: hovered && ToolTip.text.length > 0
                                    ToolTip.delay: 420

                                    indicator: Canvas {
                                        x: profileCombo.width - width - 12
                                        y: profileCombo.topPadding + (profileCombo.availableHeight - height) / 2
                                        width: 10
                                        height: 6
                                        rotation: profileCombo.popup.visible ? 180 : 0
                                        contextType: "2d"

                                        Behavior on rotation {
                                            NumberAnimation {
                                                duration: motion.stateMs
                                                easing.type: Easing.OutCubic
                                            }
                                        }

                                        onPaint: {
                                            context.reset()
                                            context.moveTo(0, 0)
                                            context.lineTo(width / 2, height)
                                            context.lineTo(width, 0)
                                            context.lineWidth = 1.7
                                            context.strokeStyle = theme.textSecondary
                                            context.lineCap = "round"
                                            context.lineJoin = "round"
                                            context.stroke()
                                        }
                                    }

                                    background: Rectangle {
                                        radius: theme.radiusControl
                                        color: theme.surfaceBase
                                        border.color: profileCombo.activeFocus ? theme.accentPrimary : theme.lineSubtle
                                        border.width: 1
                                    }

                                    delegate: ItemDelegate {
                                        id: profileDelegate
                                        required property var modelData
                                        width: profileCombo.width
                                        height: 34

                                        contentItem: Text {
                                            leftPadding: 10
                                            rightPadding: 24
                                            text: profileDelegate.modelData.label
                                            color: profileCombo.currentValue === profileDelegate.modelData.value ? theme.accentPrimary : theme.textPrimary
                                            font.pixelSize: 13
                                            font.weight: profileCombo.currentValue === profileDelegate.modelData.value ? Font.DemiBold : Font.Normal
                                            verticalAlignment: Text.AlignVCenter
                                            elide: Text.ElideRight
                                        }
                                    }

                                    popup: Popup {
                                        y: profileCombo.height + 5
                                        width: profileCombo.width
                                        implicitHeight: Math.min(contentItem.implicitHeight + 12, 240)
                                        padding: 6

                                        contentItem: ListView {
                                            clip: true
                                            implicitHeight: contentHeight
                                            model: profileCombo.popup.visible ? profileCombo.delegateModel : null
                                            currentIndex: profileCombo.highlightedIndex
                                            boundsBehavior: Flickable.StopAtBounds
                                        }

                                        background: Rectangle {
                                            color: theme.surfaceBase
                                            radius: theme.radiusCard
                                            border.color: theme.lineSoft
                                            border.width: 1
                                        }
                                    }
                                }

                                IconButton {
                                    theme: theme
                                    motion: motion
                                    buttonSize: 36
                                    iconSize: 19
                                    iconSource: Qt.resolvedUrl("../../assets/gui/icons/load_profile.svg")
                                    toolTipText: window.uiText("ui.load.selected.profile")
                                    enabled: profileCombo.currentIndex >= 0
                                    onClicked: window.openLoadProfileDialog()
                                }

                                IconButton {
                                    theme: theme
                                    motion: motion
                                    buttonSize: 36
                                    iconSize: 18
                                    iconSource: Qt.resolvedUrl("../../assets/gui/icons/reset.svg")
                                    toolTipText: window.uiText("ui.revert.selected.profile")
                                    enabled: window.value("canRevertSelectedProfile", false)
                                    onClicked: controller.revertSelectedProfile()
                                }

                                IconButton {
                                    theme: theme
                                    motion: motion
                                    buttonSize: 36
                                    iconSize: 19
                                    iconSource: Qt.resolvedUrl("../../assets/gui/icons/star.svg")
                                    checked: window.value("selectedProfileName", "") !== "" && window.value("selectedProfileName", "") === window.value("defaultProfileName", "")
                                    toolTipText: checked ? window.uiText("ui.clear.default.profile") : window.uiText("ui.set.default.profile")
                                    enabled: profileCombo.currentIndex >= 0
                                    onClicked: controller.toggleSelectedProfileDefault()
                                }

                                IconButton {
                                    theme: theme
                                    motion: motion
                                    tone: "danger"
                                    buttonSize: 36
                                    iconSize: 19
                                    iconSource: Qt.resolvedUrl("../../assets/gui/icons/clear.svg")
                                    toolTipText: window.uiText("ui.delete.profile")
                                    enabled: profileCombo.currentIndex >= 0
                                    onClicked: {
                                        deleteProfileDialog.pendingProfileName = window.value("selectedProfileName", "")
                                        deleteProfileDialog.open()
                                    }
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: theme.space8

                                Rectangle {
                                    implicitWidth: profileStatusText.implicitWidth + 20
                                    implicitHeight: 28
                                    radius: 10
                                    color: {
                                        const tone = window.value("profileStatusTone", "neutral")
                                        if (tone === "success")
                                            return theme.successSoft
                                        if (tone === "warning")
                                            return theme.warningSoft
                                        return theme.bgSubtle
                                    }
                                    border.color: {
                                        const tone = window.value("profileStatusTone", "neutral")
                                        if (tone === "success")
                                            return theme.successPrimary
                                        if (tone === "warning")
                                            return theme.warningPrimary
                                        return theme.lineSoft
                                    }
                                    border.width: 1

                                    Text {
                                        id: profileStatusText
                                        anchors.fill: parent
                                        anchors.leftMargin: 10
                                        anchors.rightMargin: 10
                                        text: window.value("profileStatusText", "")
                                        color: {
                                            const tone = window.value("profileStatusTone", "neutral")
                                            if (tone === "success")
                                                return theme.successPrimary
                                            if (tone === "warning")
                                                return theme.warningPrimary
                                            return theme.textTertiary
                                        }
                                        font.pixelSize: 12
                                        font.weight: Font.DemiBold
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                    }
                                }

                                Item {
                                    Layout.fillWidth: true
                                }
                            }
                        }
                    }
                }
            }

            PreviewStage {
                Layout.fillWidth: true
                Layout.fillHeight: true
                theme: theme
                motion: motion
                runnable: window.value("isRunnable", false)
                titleText: window.uiText("ui.live.preview")
                placeholderText: window.uiText("ui.qml.preview.placeholder")
                imageSource: window.value("previewImageSource", "")
                busy: window.value("previewBusy", false)
                busyText: window.value("previewStatusText", "")
                showCropBoxes: window.value("previewShowCropBoxes", false)
                canGoLeft: window.value("previewCanGoLeft", false)
                canGoRight: window.value("previewCanGoRight", false)
                pageText: window.value("previewPageText", "—")
                currentPageNumber: window.value("previewCurrentPageNumber", 0)
                totalPages: window.value("previewTotalPages", 0)
                cropBoxText: window.uiText("ui.show.crop.box")
                pageJumpText: window.uiText("ui.preview.page.jump")
                volumeText: window.uiText("ui.preview.volume")
                volumeOptions: window.value("previewVolumeOptions", [])
                volumeValue: window.value("previewVolume", "")
                leftActionText: window.value("previewLeftActionText", "")
                rightActionText: window.value("previewRightActionText", "")
                previewAspectRatio: window.value("previewAspectRatio", 0.72)
                coverWatermarkVisible: window.value("previewIsCoverPage", false)
                coverWatermarkText: window.value("coverWatermarkText", "")
                coverWatermarkToolTip: window.value("coverWatermarkToolTip", "")
                coverWatermarkSide: window.value("coverWatermarkSide", "left")
                onLeftRequested: controller.leftPreviewPage()
                onRightRequested: controller.rightPreviewPage()
                onPageJumpRequested: pageNumber => controller.jumpPreviewPage(pageNumber)
                onVolumeSelected: volume => controller.setPreviewVolume(volume)
                onCropBoxesToggled: controller.togglePreviewCropBoxes()
            }
        }

        SquircleCard {
            id: taskPanel
            readonly property string runState: window.value("runState", "setup")
            Layout.fillWidth: true
            Layout.preferredHeight: 138
            theme: theme
            cardRadius: theme.radiusCard

            RowLayout {
                anchors.fill: parent
                anchors.margins: theme.space16
                spacing: theme.space16

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: theme.space8

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: theme.space8

                        Rectangle {
                            Layout.alignment: Qt.AlignVCenter
                            width: 8
                            height: 8
                            radius: 4
                            color: window.runAccentColor(taskPanel.runState)

                            Behavior on color {
                                ColorAnimation {
                                    duration: motion.stateMs
                                    easing.type: Easing.OutCubic
                                }
                            }
                        }

                        Text {
                            text: window.uiText("ui.task.panel.title")
                            color: theme.textPrimary
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                            Layout.alignment: Qt.AlignVCenter
                        }

                        Text {
                            text: window.value("runStateText", "")
                            color: window.runAccentColor(taskPanel.runState)
                            font.pixelSize: 13
                            font.weight: Font.DemiBold
                            Layout.alignment: Qt.AlignVCenter
                        }

                        Text {
                            text: window.value("runProgressPercentText", "")
                            visible: window.value("runProgressValue", 0) > 0 || window.value("isRunning", false)
                            color: theme.textTertiary
                            font.pixelSize: 12
                            Layout.alignment: Qt.AlignVCenter
                        }

                        Text {
                            text: window.value("runElapsedText", "")
                            visible: text.length > 0
                            color: theme.textTertiary
                            font.pixelSize: 12
                            Layout.alignment: Qt.AlignVCenter
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 6
                        radius: 3
                        color: theme.lineSoft
                        clip: true

                        Rectangle {
                            height: parent.height
                            width: parent.width * window.value("runProgressValue", 0)
                            radius: 3
                            color: window.runAccentColor(taskPanel.runState)

                            Behavior on width {
                                NumberAnimation {
                                    duration: motion.panelMs
                                    easing.type: Easing.OutCubic
                                }
                            }

                            Behavior on color {
                                ColorAnimation {
                                    duration: motion.stateMs
                                    easing.type: Easing.OutCubic
                                }
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: window.value("runSummaryText", "")
                        color: theme.textSecondary
                        font.pixelSize: 13
                        elide: Text.ElideRight
                        maximumLineCount: 1
                    }

                    Text {
                        Layout.fillWidth: true
                        text: window.value("runDetailText", "") || window.value("logText", "")
                        visible: text.length > 0
                        color: theme.textTertiary
                        font.pixelSize: 12
                        elide: Text.ElideRight
                        maximumLineCount: 1
                    }
                }

                RowLayout {
                    spacing: theme.space8

                    IconButton {
                        theme: theme
                        motion: motion
                        buttonSize: 36
                        iconSize: 19
                        iconSource: Qt.resolvedUrl("../../assets/gui/icons/pause.svg")
                        toolTipText: window.uiText("ui.action.pause")
                        visible: window.value("canPauseRun", false)
                        onClicked: controller.requestPause()
                    }

                    IconButton {
                        theme: theme
                        motion: motion
                        buttonSize: 36
                        iconSize: 19
                        iconSource: Qt.resolvedUrl("../../assets/gui/icons/play.svg")
                        toolTipText: window.uiText("ui.action.resume")
                        visible: window.value("canResumeRun", false)
                        onClicked: controller.requestResume()
                    }

                    IconButton {
                        theme: theme
                        motion: motion
                        tone: "danger"
                        buttonSize: 36
                        iconSize: 19
                        iconSource: Qt.resolvedUrl("../../assets/gui/icons/x.svg")
                        toolTipText: window.uiText("ui.action.cancel")
                        visible: window.value("canCancelRun", false)
                        onClicked: controller.requestStop()
                    }

                    IconButton {
                        theme: theme
                        motion: motion
                        buttonSize: 36
                        iconSize: 19
                        iconSource: Qt.resolvedUrl("../../assets/gui/icons/logs.svg")
                        checked: logDrawer.visible
                        toolTipText: window.uiText("ui.logs")
                        onClicked: logDrawer.visible ? logDrawer.close() : logDrawer.open()
                    }

                    PrimaryButton {
                        theme: theme
                        motion: motion
                        prominent: !window.value("canOpenOutput", false) || taskPanel.runState === "failed" || taskPanel.runState === "stale"
                        text: taskPanel.runState === "ready" || taskPanel.runState === "setup" ? window.uiText("ui.action.start") : window.uiText("ui.action.rerun")
                        visible: !window.value("isRunning", false)
                        enabled: window.value("isRunnable", false)
                        onClicked: controller.startRun()
                    }

                    IconButton {
                        theme: theme
                        motion: motion
                        buttonSize: 36
                        iconSize: 19
                        iconSource: Qt.resolvedUrl("../../assets/gui/icons/open_output.svg")
                        checked: taskPanel.runState === "completed" || taskPanel.runState === "partial"
                        toolTipText: window.uiText("ui.action.open_output")
                        visible: window.value("canOpenOutput", false)
                        onClicked: controller.openOutputLocation()
                    }
                }
            }
        }
    }

    Drawer {
        id: logDrawer
        edge: Qt.BottomEdge
        width: window.width
        height: Math.min(390, Math.round(window.height * 0.44))
        modal: false
        interactive: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle {
            radius: theme.radiusStage
            color: theme.surfaceBase
            border.color: theme.lineSoft
            border.width: 1
        }

        contentItem: ColumnLayout {
            anchors.fill: parent
            anchors.margins: theme.space24
            spacing: theme.space12

            RowLayout {
                Layout.fillWidth: true
                spacing: theme.space8

                Text {
                    Layout.fillWidth: true
                    text: window.uiText("ui.logs")
                    color: theme.textPrimary
                    font.pixelSize: 18
                    font.weight: Font.DemiBold
                    verticalAlignment: Text.AlignVCenter
                }

                IconButton {
                    theme: theme
                    motion: motion
                    buttonSize: 34
                    iconSize: 18
                    iconSource: Qt.resolvedUrl("../../assets/gui/icons/copy.svg")
                    enabled: window.value("fullLogText", "").length > 0
                    toolTipText: window.uiText("ui.action.copy_logs")
                    onClicked: controller.copyLogsToClipboard()
                }

                IconButton {
                    theme: theme
                    motion: motion
                    buttonSize: 34
                    iconSize: 18
                    iconSource: Qt.resolvedUrl("../../assets/gui/icons/clear.svg")
                    enabled: window.value("canClearLogs", false)
                    toolTipText: window.uiText("ui.action.clear_logs")
                    onClicked: controller.clearLogs()
                }

                IconButton {
                    theme: theme
                    motion: motion
                    buttonSize: 34
                    iconSize: 18
                    iconSource: Qt.resolvedUrl("../../assets/gui/icons/x.svg")
                    toolTipText: window.uiText("ui.collapse")
                    onClicked: logDrawer.close()
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: theme.radiusCard
                color: theme.bgSubtle
                border.color: theme.lineSoft
                border.width: 1
                clip: true

                Text {
                    anchors.centerIn: parent
                    width: parent.width - 40
                    visible: window.value("logEntries", []).length === 0
                    text: window.uiText("ui.no.logs.yet")
                    color: theme.textTertiary
                    font.pixelSize: 13
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }

                ScrollView {
                    id: logScroll
                    anchors.fill: parent
                    anchors.margins: theme.space12
                    visible: window.value("logEntries", []).length > 0
                    clip: true

                    ScrollBar.vertical: ScrollBar {
                        policy: ScrollBar.AsNeeded
                        width: 6
                        opacity: active ? 0.55 : 0.28
                    }

                    Column {
                        width: logScroll.availableWidth
                        spacing: 6

                        Repeater {
                            model: window.value("logEntries", [])

                            Item {
                                required property var modelData
                                width: logScroll.availableWidth
                                implicitHeight: logLine.implicitHeight + 10

                                Rectangle {
                                    width: 5
                                    height: 5
                                    radius: 3
                                    x: 1
                                    y: 8
                                    color: window.logAccentColor(modelData.level)
                                }

                                Text {
                                    id: logLine
                                    x: 16
                                    y: 3
                                    width: parent.width - 18
                                    text: modelData.text
                                    color: window.logTextColor(modelData.level)
                                    font.pixelSize: 12
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
