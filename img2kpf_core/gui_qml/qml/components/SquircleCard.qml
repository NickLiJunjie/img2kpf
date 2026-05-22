import QtQuick

Rectangle {
    id: root
    property QtObject theme
    property int cardRadius: theme ? theme.radiusCard : 18
    property color cardColor: theme ? theme.surfaceBase : "#FFFFFF"
    property color strokeColor: theme ? theme.lineSoft : "#E5E5EA"
    property real strokeWidth: 1

    radius: cardRadius
    color: cardColor
    border.color: strokeColor
    border.width: strokeWidth
    antialiasing: true
}
