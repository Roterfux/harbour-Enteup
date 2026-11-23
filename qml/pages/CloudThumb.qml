// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0

// One photo of the account in a grid: its thumbnail as soon as it is here. Tap: full screen.
// A big tile of a video plays it silently while play is set (Wi-Fi only, smaller videos).
Item {
    id: cell
    property var app
    property real fileId: 0             // not int: ids may outgrow 32 bits
    property bool video: false
    property bool favorite: false
    property bool big: false
    property bool play: false
    property string path: ""
    property bool failed: false
    property string clipPath: ""        // the video, once fetched
    property bool clipAsked: false
    property bool clipRefused: false

    onPlayChanged: if (play && video && big && !clipAsked) {
        clipAsked = true
        app.call("cloud_clip", [fileId], function (answer) {
            if (!answer.ok) cell.clipRefused = true
        })
    }
    // selection mode of the grid: a tap selects instead of opening
    property bool selecting: false
    property bool selected: false
    // the grid's photos in order: the full-screen view swipes through them
    property var neighbours: []
    signal toggled()
    signal held()

    Component.onCompleted: {
        var known = app.thumb(fileId)
        if (known) path = known
        else if (known === "") failed = true
    }

    Connections {
        target: cell.app
        onThumbReady: if (thumbId === cell.fileId) {
            cell.path = thumbPath
            cell.failed = thumbPath === ""
        }
        onFullReady: if (fullId === cell.fileId && cell.clipAsked && result.progress === undefined) {
            if (result.video) cell.clipPath = result.video
            else cell.clipRefused = true
        }
    }

    Rectangle {
        anchors.fill: parent
        color: Theme.rgba(Theme.highlightBackgroundColor, 0.12)
        visible: picture.status !== Image.Ready
    }
    Image {
        id: picture
        anchors.fill: parent
        source: cell.path !== "" ? "file://" + cell.path : ""
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        clip: true
        smooth: true
        opacity: status === Image.Ready ? 1 : 0
        Behavior on opacity { FadeAnimator { duration: 120 } }
    }
    Loader {
        anchors.fill: parent
        clip: true
        active: cell.play && cell.clipPath !== ""
        source: "GridClip.qml"
        onLoaded: item.video = "file://" + encodeURI(cell.clipPath).replace(/#/g, "%23")
                                                                 .replace(/\?/g, "%3F")
    }
    Icon {
        anchors.centerIn: parent
        visible: cell.failed
        source: "image://theme/icon-m-image"
        color: Theme.secondaryColor
    }
    Icon {
        anchors { right: parent.right; bottom: parent.bottom; margins: Theme.paddingSmall }
        visible: cell.video
        width: Theme.iconSizeSmall
        height: width
        source: "image://theme/icon-m-video"        // the phone has no icon-s-video
    }
    Icon {
        anchors { left: parent.left; bottom: parent.bottom; margins: Theme.paddingSmall }
        visible: cell.favorite
        width: Theme.iconSizeSmall
        height: width
        source: "image://theme/icon-m-favorite-selected"
        color: "white"
    }
    Rectangle {
        anchors.fill: parent
        color: Theme.highlightBackgroundColor
        opacity: cell.selected ? 0.45
               : touch.pressed && touch.containsMouse ? Theme.highlightBackgroundOpacity : 0
    }
    // selection mark: a ring while selecting, filled with a tick once selected
    Rectangle {
        visible: cell.selecting
        anchors { right: parent.right; top: parent.top; margins: Theme.paddingSmall }
        width: Theme.iconSizeSmall + Theme.paddingSmall
        height: width
        radius: width / 2
        color: cell.selected ? Theme.highlightColor : Theme.rgba("black", 0.35)
        border.color: "white"
        border.width: 2
        Icon {
            anchors.centerIn: parent
            visible: cell.selected
            width: Theme.iconSizeSmall
            height: width
            source: "image://theme/icon-s-installed"
            color: "white"
        }
    }
    MouseArea {
        id: touch
        anchors.fill: parent
        onClicked: {
            if (cell.selecting) cell.toggled()
            else pageStack.push(Qt.resolvedUrl("PreviewPage.qml"),
                                { app: cell.app, cloudId: cell.fileId, fallback: cell.path,
                                  ids: cell.neighbours,
                                  index: cell.neighbours.indexOf(cell.fileId) })
        }
        onPressAndHold: cell.held()
    }
}
