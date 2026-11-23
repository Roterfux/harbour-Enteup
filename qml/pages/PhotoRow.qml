// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0

// One photo or video: a small preview, its name, when it was taken and how its upload went.
// Tap: the photo full screen (a video opens in the phone's player).
ListItem {
    id: row
    property string path
    property string name
    property bool video: false
    property real taken: 0          // epoch seconds, 0 = unknown
    property string status: ""      // done | skipped | failed | "" (no mark)
    property string detail: ""

    contentHeight: Math.max(Theme.itemSizeLarge, texts.height + 2 * Theme.paddingMedium)

    function fileUrl(path) {
        return "file://" + encodeURI(path).replace(/#/g, "%23").replace(/\?/g, "%3F")
    }

    function when(seconds) {
        return seconds > 0 ? Qt.formatDateTime(new Date(seconds * 1000), "d MMM yyyy, hh:mm")
                           : qsTr("Time unknown")
    }

    function open() {
        if (row.video) {
            Qt.openUrlExternally(fileUrl(row.path))
        } else {
            pageStack.push(Qt.resolvedUrl("PreviewPage.qml"),
                           { path: row.path, name: row.name, taken: row.taken })
        }
    }

    onClicked: open()

    Rectangle {
        id: frame
        x: Theme.horizontalPageMargin
        anchors.verticalCenter: parent.verticalCenter
        width: Theme.itemSizeLarge - 2 * Theme.paddingSmall
        height: width
        color: Theme.rgba(Theme.highlightBackgroundColor, 0.15)
        clip: true

        Image {
            id: thumb
            anchors.fill: parent
            visible: status === Image.Ready
            source: !row.video && row.path !== "" ? row.fileUrl(row.path) : ""
            sourceSize.width: width * 2
            sourceSize.height: height * 2
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            autoTransform: true
            smooth: true
        }
        Icon {
            anchors.centerIn: parent
            visible: !thumb.visible
            source: row.video ? "image://theme/icon-m-video"
                  : thumb.status === Image.Loading ? "" : "image://theme/icon-m-image"
            color: Theme.secondaryColor
        }
        Icon {
            id: mark
            anchors { right: parent.right; bottom: parent.bottom; margins: Theme.paddingSmall / 2 }
            visible: row.status !== ""
            width: Theme.iconSizeSmall
            height: width
            source: row.status === "done" ? "image://theme/icon-s-installed"
                  : row.status === "failed" ? "image://theme/icon-s-warning"
                  : "image://theme/icon-s-time"
            color: row.status === "failed" ? Theme.errorColor : Theme.primaryColor
        }
    }

    Column {
        id: texts
        anchors { left: frame.right; leftMargin: Theme.paddingLarge
                  right: parent.right; rightMargin: Theme.horizontalPageMargin
                  verticalCenter: parent.verticalCenter }
        Label {
            width: parent.width
            truncationMode: TruncationMode.Fade
            color: row.highlighted ? Theme.highlightColor : Theme.primaryColor
            text: row.when(row.taken)
        }
        Label {
            width: parent.width
            truncationMode: TruncationMode.Fade
            font.pixelSize: Theme.fontSizeExtraSmall
            color: Theme.secondaryColor
            text: row.name
        }
        Label {
            width: parent.width
            wrapMode: Text.Wrap
            visible: text !== ""
            font.pixelSize: Theme.fontSizeExtraSmall
            color: row.status === "failed" ? Theme.errorColor : Theme.secondaryHighlightColor
            text: row.detail
        }
    }
}
