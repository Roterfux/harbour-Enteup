// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0

// Everything uploaded from this phone, newest capture first, grouped by the day taken
Page {
    id: page
    property var app
    allowedOrientations: Orientation.All
    property bool busy: true

    function load() {
        busy = true
        app.call("history", [], function (list) {
            photos.clear()
            for (var i = 0; i < list.length; ++i) {
                var e = list[i]
                e.day = e.taken > 0 ? Qt.formatDate(new Date(e.taken * 1000), Qt.DefaultLocaleLongDate)
                                    : qsTr("Time unknown")
                photos.append(e)
            }
            busy = false
        })
    }

    Component.onCompleted: load()

    ListModel { id: photos }

    SilicaListView {
        id: view
        anchors.fill: parent
        model: photos

        PullDownMenu {
            MenuItem {
                text: qsTr("Load again")
                onClicked: page.load()
            }
        }

        header: PageHeader {
            title: qsTr("Uploaded photos")
            description: page.busy ? "" : qsTr("%n file(s)", "", view.count)
        }

        section.property: "day"
        section.delegate: SectionHeader { text: section }

        delegate: PhotoRow {
            path: model.path
            name: model.name
            video: model.video
            taken: model.taken
        }

        BusyIndicator {
            anchors.centerIn: parent
            size: BusyIndicatorSize.Large
            running: page.busy
        }
        ViewPlaceholder {
            enabled: !page.busy && view.count === 0
            text: qsTr("Nothing uploaded yet")
        }
        VerticalScrollDecorator { }
    }
}
