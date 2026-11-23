// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0

// Pick a folder to upload from: tap a folder to open it, "Add this folder" to take it
Page {
    id: page
    property var app
    allowedOrientations: Orientation.All

    property string path: ""        // "" = the starting points
    property string folderLabel: ""
    property string parentPath: ""
    property int here: 0            // photos and videos directly in this folder
    property bool busy: true
    property string problem: ""

    function open(target) {
        busy = true
        problem = ""
        app.call("folders", [target], function (result) {
            page.path = result.path
            page.folderLabel = result.label
            page.parentPath = result.parent
            page.here = result.here
            page.problem = result.error
            dirModel.clear()
            for (var i = 0; i < result.dirs.length; ++i) dirModel.append(result.dirs[i])
            page.busy = false
            view.positionViewAtBeginning()
        })
    }

    Component.onCompleted: open("")

    ListModel { id: dirModel }

    SilicaListView {
        id: view
        anchors.fill: parent
        model: dirModel

        PullDownMenu {
            visible: page.path !== "" && page.problem === ""
            MenuItem {
                text: qsTr("Add this folder")
                onClicked: app.call("add_source", [page.path], function (state) {
                    app.st = state
                    pageStack.pop()
                })
            }
        }

        header: Column {
            width: view.width
            PageHeader {
                title: page.path === "" ? qsTr("Add folder") : page.folderLabel
                description: page.path === "" ? qsTr("Where are the photos?")
                           : qsTr("%n photo(s) or video(s) here, not counting subfolders", "", page.here)
            }
            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                wrapMode: Text.Wrap
                visible: text !== ""
                color: Theme.errorColor
                text: page.problem
            }
            Button {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: page.path !== "" && page.problem === ""
                text: qsTr("Add this folder")
                onClicked: app.call("add_source", [page.path], function (state) {
                    app.st = state
                    pageStack.pop()
                })
            }
            BackgroundItem {
                visible: page.path !== ""
                width: parent.width
                height: Theme.itemSizeSmall
                onClicked: page.open(page.parentPath)
                Icon {
                    id: up
                    x: Theme.horizontalPageMargin
                    anchors.verticalCenter: parent.verticalCenter
                    source: "image://theme/icon-m-back"
                }
                Label {
                    anchors { left: up.right; leftMargin: Theme.paddingMedium
                              verticalCenter: parent.verticalCenter }
                    color: Theme.secondaryColor
                    text: page.parentPath === "" ? qsTr("All places") : qsTr("Up")
                }
            }
        }

        delegate: BackgroundItem {
            height: Theme.itemSizeSmall
            onClicked: page.open(model.path)
            Icon {
                id: icon
                x: Theme.horizontalPageMargin
                anchors.verticalCenter: parent.verticalCenter
                source: page.path === "" && model.label.indexOf("SD card") === 0
                        ? "image://theme/icon-m-sd-card" : "image://theme/icon-m-file-folder"
            }
            Label {
                anchors { left: icon.right; leftMargin: Theme.paddingMedium
                          right: parent.right; rightMargin: Theme.horizontalPageMargin
                          verticalCenter: parent.verticalCenter }
                truncationMode: TruncationMode.Fade
                text: model.name
            }
        }

        BusyIndicator {
            anchors.centerIn: parent
            size: BusyIndicatorSize.Large
            running: page.busy
        }
        ViewPlaceholder {
            enabled: !page.busy && view.count === 0 && page.problem === ""
            text: qsTr("No folders inside")
        }
        VerticalScrollDecorator { }
    }
}
