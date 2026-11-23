// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0

// One memory as a title picture at the top of the timeline: its cover photo with the title
// over it. Tap: the memory's photos.
BackgroundItem {
    id: cover
    property var app
    property real fileId: 0             // not int: ids may outgrow 32 bits
    property string title
    property string subtitle
    property string path: ""

    Component.onCompleted: {
        var known = app.thumb(fileId)
        if (known) path = known
    }
    Connections {
        target: cover.app
        onThumbReady: if (thumbId === cover.fileId) cover.path = thumbPath
    }

    Rectangle {
        anchors.fill: parent
        color: Theme.rgba(Theme.highlightBackgroundColor, 0.12)
    }
    Image {
        anchors.fill: parent
        source: cover.path !== "" ? "file://" + cover.path : ""
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        clip: true
        smooth: true
        opacity: status === Image.Ready ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 150 } }
    }
    // the title stays readable on light photos
    Rectangle {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: parent.height * 0.55
        gradient: Gradient {
            GradientStop { position: 0; color: "transparent" }
            GradientStop { position: 1; color: Qt.rgba(0, 0, 0, 0.75) }
        }
    }
    Column {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom
                  margins: Theme.paddingMedium }
        Label {
            width: parent.width
            wrapMode: Text.Wrap
            maximumLineCount: 2
            font.pixelSize: Theme.fontSizeSmall
            font.bold: true
            color: "white"
            text: cover.title
        }
        Label {
            width: parent.width
            truncationMode: TruncationMode.Fade
            font.pixelSize: Theme.fontSizeExtraSmall
            color: Qt.rgba(1, 1, 1, 0.8)
            text: cover.subtitle
        }
    }
}
