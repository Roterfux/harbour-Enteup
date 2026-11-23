// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0

// The folders "Upload new from sources" takes photos from. Each is switched on or off and
// goes to the main album or to an album of its own (press and hold).
Page {
    id: page
    property var app
    allowedOrientations: Orientation.All
    property bool busy: true
    property string notice: ""

    function load() {
        busy = true
        app.call("sources", [app.includeVideos], function (list) {
            sourceModel.clear()
            for (var i = 0; i < list.length; ++i) sourceModel.append(list[i])
            busy = false
        })
        app.call("suggestions", [app.includeVideos], function (list) {
            suggestionModel.clear()
            for (var i = 0; i < list.length; ++i) suggestionModel.append(list[i])
        })
    }

    function refreshState() {
        app.call("state", [], function (state) { app.st = state })
    }

    function chooseAlbum(path) {
        var picker = pageStack.push(Qt.resolvedUrl("AlbumsPage.qml"), { app: page.app, pick: true })
        picker.picked.connect(function (albumId, name) {
            app.call("set_source_album", [path, albumId, name], function () { page.load() })
        })
    }

    function uploadOne(path) {
        notice = ""
        app.uploadError = ""
        app.call("upload_sources", [app.includeVideos, path], function (result) {
            page.notice = result.noAlbum.length > 0 ? qsTr("Choose an album for this folder first")
                        : result.queued === 0 ? qsTr("Nothing new in this folder")
                        : qsTr("%n file(s) queued", "", result.queued)
        })
    }

    // back from the folder browser or the album list: show the new state
    onStatusChanged: if (status === PageStatus.Activating && !busy) load()
    Component.onCompleted: load()

    ListModel { id: sourceModel }
    ListModel { id: suggestionModel }

    SilicaListView {
        id: view
        anchors.fill: parent
        model: sourceModel

        PullDownMenu {
            MenuItem {
                text: qsTr("Add folder")
                onClicked: pageStack.push(Qt.resolvedUrl("FolderPage.qml"), { app: page.app })
            }
            MenuItem {
                enabled: app.st.signedIn && !app.up.active
                text: qsTr("Upload new from all")
                onClicked: {
                    page.notice = ""
                    app.uploadAll(function (text) { page.notice = text })
                }
            }
        }

        header: Column {
            width: view.width
            PageHeader {
                title: qsTr("Upload sources")
                description: app.st.albumName !== "" ? qsTr("Main album: %1").arg(app.st.albumName)
                                                     : qsTr("No main album chosen")
            }
            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                wrapMode: Text.Wrap
                visible: text !== ""
                color: Theme.highlightColor
                horizontalAlignment: Text.AlignHCenter
                text: page.notice
            }
        }

        delegate: ListItem {
            id: row
            contentHeight: Math.max(Theme.itemSizeMedium, texts.height + 2 * Theme.paddingMedium)
            menu: ContextMenu {
                MenuItem {
                    text: qsTr("Upload new from here")
                    enabled: model.exists && !app.up.active
                    onClicked: page.uploadOne(model.path)
                }
                MenuItem {
                    text: qsTr("Upload to album…")
                    enabled: app.st.signedIn
                    onClicked: page.chooseAlbum(model.path)
                }
                MenuItem {
                    visible: model.own
                    text: qsTr("Upload to the main album")
                    onClicked: app.call("set_source_album", [model.path, 0, ""],
                                        function () { page.load() })
                }
                MenuItem {
                    text: qsTr("Remove")
                    onClicked: row.remorseAction(qsTr("Removing"), function () {
                        app.call("remove_source", [model.path], function (state) {
                            app.st = state
                            page.load()
                        })
                    })
                }
            }

            Column {
                id: texts
                anchors { left: parent.left; leftMargin: Theme.horizontalPageMargin
                          right: toggle.left; verticalCenter: parent.verticalCenter }
                Label {
                    width: parent.width
                    truncationMode: TruncationMode.Fade
                    color: !model.on ? Theme.secondaryColor
                         : row.highlighted ? Theme.highlightColor : Theme.primaryColor
                    text: model.label
                }
                Label {
                    width: parent.width
                    truncationMode: TruncationMode.Fade
                    font.pixelSize: Theme.fontSizeExtraSmall
                    color: Theme.secondaryHighlightColor
                    text: model.albumName !== ""
                          ? (model.own ? qsTr("→ %1").arg(model.albumName)
                                       : qsTr("→ %1 (main album)").arg(model.albumName))
                          : qsTr("→ no album yet")
                }
                Label {
                    width: parent.width
                    wrapMode: Text.Wrap
                    font.pixelSize: Theme.fontSizeExtraSmall
                    color: model.exists ? Theme.secondaryColor : Theme.errorColor
                    text: !model.exists ? qsTr("Folder not found")
                          : qsTr("%1 new of %2").arg(model.fresh).arg(model.total)
                            + (model.broken > 0 ? ", " + qsTr("%n empty or broken, left out", "", model.broken) : "")
                }
            }

            Switch {
                id: toggle
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                automaticCheck: false
                checked: model.on
                onClicked: {
                    var path = model.path
                    var on = !checked
                    sourceModel.setProperty(index, "on", on)
                    app.call("set_source", [path, on], function (state) { app.st = state })
                }
            }
        }

        footer: Column {
            width: view.width
            visible: suggestionModel.count > 0
            SectionHeader { text: qsTr("Suggestions") }
            Repeater {
                model: suggestionModel
                delegate: ListItem {
                    contentHeight: Theme.itemSizeSmall
                    Icon {
                        id: plus
                        x: Theme.horizontalPageMargin
                        anchors.verticalCenter: parent.verticalCenter
                        source: "image://theme/icon-m-add"
                    }
                    Label {
                        anchors { left: plus.right; leftMargin: Theme.paddingMedium
                                  right: number.left; rightMargin: Theme.paddingMedium
                                  verticalCenter: parent.verticalCenter }
                        truncationMode: TruncationMode.Fade
                        text: model.label
                    }
                    Label {
                        id: number
                        anchors { right: parent.right; rightMargin: Theme.horizontalPageMargin
                                  verticalCenter: parent.verticalCenter }
                        font.pixelSize: Theme.fontSizeExtraSmall
                        color: Theme.secondaryColor
                        text: model.total >= 9999 ? "9999+" : model.total
                    }
                    onClicked: app.call("add_source", [model.path], function (state) {
                        app.st = state
                        page.load()
                    })
                }
            }
        }

        BusyIndicator {
            anchors.centerIn: parent
            size: BusyIndicatorSize.Large
            running: page.busy && view.count === 0
        }
        ViewPlaceholder {
            enabled: !page.busy && view.count === 0
            text: qsTr("No sources")
            hintText: qsTr("Pull down to add a folder")
        }
        VerticalScrollDecorator { }
    }
}
