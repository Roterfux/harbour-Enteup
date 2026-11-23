// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0

// The account's albums (hidden ones left out), newest photo first
Page {
    id: page
    property var app
    allowedOrientations: Orientation.All
    property bool loaded: false

    function load() {
        app.call("cloud_albums", [], function (found) {
            albumModel.clear()
            for (var i = 0; i < found.length; ++i) albumModel.append(found[i])
            page.loaded = true
        })
    }

    Component.onCompleted: load()
    Connections {
        target: page.app
        onCloudSynced: page.load()
        onPhotosRemoved: page.load()
    }

    ListModel { id: albumModel }

    SilicaListView {
        id: view
        anchors.fill: parent
        model: albumModel
        header: Column {
            width: view.width
            PageHeader {
                title: qsTr("Albums in Ente")
                description: page.loaded ? qsTr("%n album(s)", "", view.count) : ""
            }
            // sessions from before 0.6.0 did not keep the key for shared albums
            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                visible: (app.cloud.locked || 0) > 0
                wrapMode: Text.Wrap
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.highlightColor
                text: qsTr("%n album(s) shared with you cannot be opened yet: sign out and in "
                           + "again once (main page, pull-down menu).", "", app.cloud.locked || 0)
            }
        }

        section.property: "section"
        section.delegate: SectionHeader {
            text: section === "shared" ? qsTr("Shared with you") : qsTr("Your albums")
        }

        delegate: ListItem {
            id: row
            contentHeight: Theme.itemSizeExtraLarge
            onClicked: pageStack.push(Qt.resolvedUrl("CloudPage.qml"),
                                      { app: page.app, albumId: model.id, title: model.name,
                                        shared: model.owner !== "", albumType: model.type })

            CloudThumb {
                id: cover
                app: page.app
                x: Theme.horizontalPageMargin
                anchors.verticalCenter: parent.verticalCenter
                width: Theme.itemSizeExtraLarge - 2 * Theme.paddingSmall
                height: width
                fileId: model.cover
                enabled: false              // the row opens the album, not the photo
                visible: model.cover > 0
            }
            Column {
                anchors { left: cover.right; leftMargin: Theme.paddingLarge
                          right: parent.right; rightMargin: Theme.horizontalPageMargin
                          verticalCenter: parent.verticalCenter }
                Label {
                    width: parent.width
                    truncationMode: TruncationMode.Fade
                    color: row.highlighted ? Theme.highlightColor : Theme.primaryColor
                    text: model.name
                }
                Label {
                    width: parent.width
                    truncationMode: TruncationMode.Fade
                    font.pixelSize: Theme.fontSizeExtraSmall
                    color: Theme.secondaryColor
                    text: (model.owner !== "" ? qsTr("from %1").arg(model.owner) + " · " : "")
                          + qsTr("%n photo(s)", "", model.count)
                          + (model.archived ? " · " + qsTr("archived") : "")
                          + (model.newest > 0 ? " · " + Qt.formatDate(new Date(model.newest * 1000),
                                                                     "MMM yyyy") : "")
                }
            }
        }

        BusyIndicator {
            anchors.centerIn: parent
            size: BusyIndicatorSize.Large
            running: !page.loaded
        }
        ViewPlaceholder {
            enabled: page.loaded && view.count === 0
            text: qsTr("No albums")
        }
        VerticalScrollDecorator { }
    }
}
