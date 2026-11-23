// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0

// Free up space on the phone: photos and videos in the upload sources, older than a chosen
// age, whose exact content is in one of the user's own albums in Ente. Nothing is deleted
// before the list is shown and the delete is confirmed; each file is checked once more
// just before it goes.
Page {
    id: page
    property var app
    property int days: 30
    property string stage: ""           // "", "checking", "deleting"
    property real progress: 0
    property int total: 0
    property var files: []
    property var skip: ({})             // path -> true: left on the phone
    property int skipRevision: 0
    property bool checked: false        // a check has finished
    property string notice: ""
    property int notInEnte: 0
    property int looked: 0

    allowedOrientations: Orientation.All
    backNavigation: stage === ""

    function chosen() {
        var paths = []
        for (var i = 0; i < files.length; ++i)
            if (!skip[files[i].path]) paths.push(files[i].path)
        return paths
    }
    function chosenBytes() {
        var sum = 0
        for (var i = 0; i < files.length; ++i)
            if (!skip[files[i].path]) sum += files[i].size
        return sum
    }
    readonly property int chosenCount: skipRevision >= 0 ? chosen().length : 0
    readonly property real chosenSize: skipRevision >= 0 ? chosenBytes() : 0

    function check() {
        stage = "checking"
        progress = 0
        total = 0
        notice = ""
        files = []
        skip = ({})
        skipRevision += 1
        app.call("cleanup_scan", [days])
    }

    function remove() {
        var paths = chosen()
        if (paths.length === 0) return
        remorse.execute(qsTr("Deleting %n file(s) from the phone", "", paths.length), function () {
            page.stage = "deleting"
            page.notice = ""
            page.app.call("cleanup_delete", [paths])
        })
    }

    Connections {
        target: page.app
        onCleanupReady: {
            if (step === 0) {
                if (result.progress !== undefined) {
                    page.progress = result.progress
                    page.total = result.total
                    return
                }
                page.stage = ""
                page.checked = !result.stopped && !result.error
                page.files = result.files || []
                page.notInEnte = result.notInEnte || 0
                page.looked = result.checked || 0
                page.notice = result.error ? result.error
                            : result.stopped ? qsTr("Stopped") : ""
            } else {
                page.stage = ""
                var parts = []
                if (result.done > 0)
                    parts.push(qsTr("%n file(s) deleted, %1 freed", "", result.done)
                               .arg(page.app.bytes(result.freed)))
                if (result.kept > 0)
                    parts.push(qsTr("%n kept: changed since, or no longer in Ente", "", result.kept))
                if (result.error) parts.push(result.error)
                page.notice = parts.join(". ")
                // what went is off the list
                var left = []
                for (var i = 0; i < page.files.length; ++i)
                    if (page.skip[page.files[i].path]) left.push(page.files[i])
                page.files = left
                page.checked = false
            }
        }
    }

    RemorsePopup { id: remorse }

    SilicaListView {
        id: view
        anchors.fill: parent
        model: page.files

        header: Column {
            width: view.width
            spacing: Theme.paddingMedium
            PageHeader { title: qsTr("Free up space") }
            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                wrapMode: Text.Wrap
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.secondaryHighlightColor
                text: qsTr("Deletes photos and videos from the phone that are safely in Ente: "
                           + "a file with exactly the same content is in one of your albums "
                           + "(not only in Ente's trash). Only the upload sources are looked "
                           + "at. You see the list before anything is deleted.")
            }
            ComboBox {
                enabled: page.stage === ""
                label: qsTr("Older than")
                currentIndex: 0
                menu: ContextMenu {
                    MenuItem { text: qsTr("1 month"); onClicked: page.days = 30 }
                    MenuItem { text: qsTr("3 months"); onClicked: page.days = 91 }
                    MenuItem { text: qsTr("1 year"); onClicked: page.days = 365 }
                    MenuItem { text: qsTr("any age"); onClicked: page.days = 0 }
                }
            }
            Button {
                anchors.horizontalCenter: parent.horizontalCenter
                text: page.stage === "checking" ? qsTr("Stop") : qsTr("Check")
                enabled: page.stage !== "deleting"
                onClicked: page.stage === "checking" ? page.app.call("cleanup_cancel")
                                                     : page.check()
            }
            ProgressBar {
                width: parent.width
                visible: page.stage !== ""
                indeterminate: page.stage === "deleting" || page.total === 0
                minimumValue: 0
                maximumValue: Math.max(1, page.total)
                value: page.progress
                label: page.stage === "deleting" ? qsTr("Checking again and deleting")
                       : page.total === 0 ? qsTr("Reading what is in Ente")
                       : qsTr("Comparing %1 of %2").arg(page.progress).arg(page.total)
            }
            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                visible: text !== ""
                wrapMode: Text.Wrap
                color: Theme.highlightColor
                text: page.notice
            }
            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                visible: page.checked && page.stage === ""
                wrapMode: Text.Wrap
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.highlightColor
                text: page.files.length === 0
                      ? qsTr("Nothing to delete: of %n file(s) looked at, none is in Ente yet",
                             "", page.looked)
                      : qsTr("%n file(s) in Ente, %1. Tap one to keep it on the phone.", "",
                             page.files.length).arg(page.app.bytes(page.chosenSize))
                        + (page.notInEnte > 0
                           ? " " + qsTr("%n not in Ente stay anyway.", "", page.notInEnte) : "")
            }
            Button {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: page.checked && page.files.length > 0
                enabled: page.stage === "" && page.chosenCount > 0
                text: qsTr("Delete %n from the phone", "", page.chosenCount)
                onClicked: page.remove()
            }
            Item { width: 1; height: Theme.paddingMedium }
        }

        delegate: ListItem {
            id: item
            readonly property bool kept: page.skipRevision >= 0 && page.skip[modelData.path] === true
            contentHeight: Theme.itemSizeLarge
            enabled: page.stage === ""
            onClicked: {
                if (page.skip[modelData.path]) delete page.skip[modelData.path]
                else page.skip[modelData.path] = true
                page.skipRevision += 1
            }

            Image {
                id: preview
                x: Theme.horizontalPageMargin
                anchors.verticalCenter: parent.verticalCenter
                width: Theme.itemSizeLarge - Theme.paddingMedium
                height: width
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                clip: true
                visible: !modelData.video
                source: modelData.video ? "" : "file://" + modelData.path
                sourceSize.width: width
                sourceSize.height: height
                opacity: item.kept ? 0.3 : 1
            }
            Icon {
                anchors.centerIn: preview
                visible: modelData.video
                source: "image://theme/icon-m-video"
                opacity: item.kept ? 0.3 : 1
            }
            Column {
                anchors { left: preview.right; leftMargin: Theme.paddingMedium
                          right: mark.left; rightMargin: Theme.paddingSmall
                          verticalCenter: parent.verticalCenter }
                opacity: item.kept ? 0.4 : 1
                Label {
                    width: parent.width
                    truncationMode: TruncationMode.Fade
                    text: modelData.name
                }
                Label {
                    width: parent.width
                    truncationMode: TruncationMode.Fade
                    font.pixelSize: Theme.fontSizeExtraSmall
                    color: Theme.secondaryColor
                    text: Qt.formatDate(new Date(modelData.taken * 1000), "d MMM yyyy") + " · "
                          + page.app.bytes(modelData.size) + " · " + qsTr("in %1").arg(modelData.album)
                }
            }
            Label {
                id: mark
                anchors { right: parent.right; rightMargin: Theme.horizontalPageMargin
                          verticalCenter: parent.verticalCenter }
                font.pixelSize: Theme.fontSizeExtraSmall
                color: item.kept ? Theme.highlightColor : Theme.secondaryColor
                text: item.kept ? qsTr("keep") : qsTr("delete")
            }
        }
        VerticalScrollDecorator { }
    }
}
