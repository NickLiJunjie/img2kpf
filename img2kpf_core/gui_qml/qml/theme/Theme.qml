import QtQuick

QtObject {
    property bool dark: false
    readonly property color bgBase: dark ? "#101114" : "#F5F5F7"
    readonly property color bgSubtle: dark ? "#17191D" : "#FBFBFD"
    readonly property color surfaceBase: dark ? "#1E2026" : "#FFFFFF"
    readonly property color lineSubtle: dark ? "#3A3D46" : "#D2D2D7"
    readonly property color lineSoft: dark ? "#2B2E36" : "#E5E5EA"
    readonly property color textPrimary: dark ? "#F5F5F7" : "#1D1D1F"
    readonly property color textSecondary: dark ? "#B7BBC6" : "#6E6E73"
    readonly property color textTertiary: dark ? "#858A96" : "#8E8E93"
    readonly property color accentPrimary: "#0071E3"
    readonly property color accentHover: "#0062C4"
    readonly property color accentSoft: dark ? "#123456" : "#EAF3FF"
    readonly property color successPrimary: "#1F9D5A"
    readonly property color successSoft: dark ? "#12301F" : "#EAF7EF"
    readonly property color warningPrimary: "#B97000"
    readonly property color warningSoft: dark ? "#33260B" : "#FFF4D6"
    readonly property color dangerBase: "#D92D20"
    readonly property color dangerSoft: dark ? "#351716" : "#FDECEC"
    readonly property int space4: 4
    readonly property int space8: 8
    readonly property int space12: 12
    readonly property int space16: 16
    readonly property int space24: 24
    readonly property int space32: 32
    readonly property int radiusControl: 14
    readonly property int radiusCard: 18
    readonly property int radiusStage: 24
}
