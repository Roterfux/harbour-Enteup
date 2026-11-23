// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0

// The account's own albums: tap one to upload into it, or make a new one
Page {
    id: page
    property var app
    allowedOrientations: Orientation.All

    property bool busy: true
    property string problem: ""
    property bool created: false        // a new album was asked for: leave when it is there
    // pick: choose an album for someone else (a source) instead of making it the main one
    property bool pick: false
    property string pickTitle: qsTr("Album for this folder")
    signal picked(var albumId, string name)
    property string newName: ""

    function typeName(type) {
        return type === "favorites" ? qsTr("Favorites")
             : type === "uncategorized" ? qsTr("Uncategorized")
             : type === "folder" ? qsTr("Device folder") : ""
    }

    Component.onCompleted: app.call("albums")

    Connections {
        target: page.app
        onAlbumsLoaded: {
            page.busy = false
            page.problem = message
            if (message !== "") {
                page.created = false
                return
            }
            albumModel.clear()
            for (var i = 0; i < list.length; ++i) albumModel.append(list[i])
            if (page.created) {
                page.created = false
                if (page.pick && made) page.picked(made, page.newName)
                pageStack.pop(pageStack.previousPage(page))
            }
        }
    }

    ListModel { id: albumModel }

    Component {
        id: newAlbum
        Dialog {
            property alias name: nameField.text
            canAccept: nameField.text.trim() !== ""
            Column {
                width: parent.width
                DialogHeader { acceptText: qsTr("Create") }
                TextField {
                    id: nameField
                    focus: true
                    label: qsTr("Album name")
                    placeholderText: label
                    EnterKey.enabled: text.trim() !== ""
                    EnterKey.iconSource: "image://theme/icon-m-enter-accept"
                    EnterKey.onClicked: accept()
                }
            }
        }
    }

    SilicaListView {
        id: view
        anchors.fill: parent
        model: albumModel

        PullDownMenu {
            MenuItem {
                text: qsTr("Load again")
                onClicked: { page.busy = true; page.problem = ""; app.call("albums") }
            }
            MenuItem {
                text: qsTr("New album")
                onClicked: {
                    var dialog = pageStack.push(newAlbum)
                    dialog.accepted.connect(function () {
                        page.busy = true
                        page.problem = ""
                        page.created = true
                        page.newName = dialog.name.trim()
                        app.call("create_album", [dialog.name, !page.pick])
                    })
                }
            }
        }

        header: Column {
            width: view.width
            PageHeader {
                title: page.pick ? page.pickTitle : qsTr("Main album")
                description: page.pick ? "" : qsTr("For chosen files and sources without an album of their own")
            }
            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                wrapMode: Text.Wrap
                color: Theme.errorColor
                visible: text !== ""
                text: page.problem
            }
        }

        delegate: ListItem {
            id: row
            contentHeight: Theme.itemSizeSmall
            highlighted: down || (!page.pick && model.id === app.st.albumId)
            Label {
                anchors { left: parent.left; leftMargin: Theme.horizontalPageMargin
                          right: kind.left; rightMargin: Theme.paddingMedium
                          verticalCenter: parent.verticalCenter }
                truncationMode: TruncationMode.Fade
                color: row.highlighted ? Theme.highlightColor : Theme.primaryColor
                text: model.name
            }
            Label {
                id: kind
                anchors { right: parent.right; rightMargin: Theme.horizontalPageMargin
                          verticalCenter: parent.verticalCenter }
                font.pixelSize: Theme.fontSizeExtraSmall
                color: Theme.secondaryColor
                text: page.typeName(model.type)
            }
            onClicked: {
                if (page.pick) page.picked(model.id, model.name)
                else app.callState("set_album", [model.id, model.name])
                pageStack.pop()
            }
        }

        BusyIndicator {
            anchors.centerIn: parent
            size: BusyIndicatorSize.Large
            running: page.busy
        }
        ViewPlaceholder {
            enabled: !page.busy && view.count === 0 && page.problem === ""
            text: qsTr("No albums yet")
            hintText: qsTr("Pull down to make one")
        }
        VerticalScrollDecorator { }
    }
}
