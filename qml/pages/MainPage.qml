// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0
import Sailfish.Pickers 1.0

Page {
    id: page
    property var app
    allowedOrientations: Orientation.All

    readonly property bool canUpload: app.st.signedIn && app.st.albumId > 0
    property string notice: ""

    // back on this page: the space may have changed (at most once a minute)
    onStatusChanged: if (status === PageStatus.Active) app.refreshSpace(false)

    // What the last automatic backup run did, in one line
    function autoLastText() {
        var last = app.st.autoLast || {}
        if (!last.at) return qsTr("On: every 15 minutes. It has not run yet")
        var when = Qt.formatDateTime(new Date(last.at * 1000), "d MMM, hh:mm")
        if (!last.ran) return qsTr("Last check %1: %2").arg(when).arg(last.reason)
        var parts = [qsTr("%n uploaded", "", last.uploaded)]
        if (last.failed > 0) parts.push(qsTr("%n failed", "", last.failed))
        if (last.error) parts.push(last.error)
        if (last.reason) parts.push(last.reason)
        return qsTr("Last run %1: %2").arg(when).arg(parts.join(", "))
    }

    function plain(url) {
        var text = String(url)
        return text.indexOf("file://") === 0 ? decodeURIComponent(text.substring(7)) : text
    }

    function picked(content) {
        var list = []
        for (var i = 0; i < content.count; ++i) {
            var item = content.get(i)
            list.push(item.filePath ? String(item.filePath) : plain(item.url))
        }
        app.uploadFiles(list)
    }

    Component {
        id: photoPicker
        MultiImagePickerDialog { onAccepted: page.picked(selectedContent) }
    }
    Component {
        id: videoPicker
        MultiVideoPickerDialog { onAccepted: page.picked(selectedContent) }
    }

    SilicaListView {
        id: view
        anchors.fill: parent
        model: app.results

        PullDownMenu {
            MenuItem {
                text: qsTr("About")
                onClicked: pageStack.push(Qt.resolvedUrl("AboutPage.qml"), { app: page.app })
            }
            MenuItem {
                visible: app.st.uploaded > 0
                text: qsTr("Uploaded photos")
                onClicked: pageStack.push(Qt.resolvedUrl("HistoryPage.qml"), { app: page.app })
            }
            MenuItem {
                visible: app.st.signedIn
                text: qsTr("Sign out")
                onClicked: signOutRemorse.execute(qsTr("Signing out"),
                                                  function () { app.callState("sign_out") })
            }
            MenuItem {
                visible: app.up.active
                text: qsTr("Stop uploading")
                onClicked: app.call("cancel")
            }
        }
        RemorsePopup { id: signOutRemorse }

        header: Column {
            width: view.width
            spacing: Theme.paddingMedium

            PageHeader {
                title: "Enteup"
                description: app.st.signedIn ? app.st.email : ""
            }

            BusyIndicator {
                anchors.horizontalCenter: parent.horizontalCenter
                size: BusyIndicatorSize.Large
                running: !app.ready && app.engineError === ""
                visible: running
            }

            // Problems that stop everything: shown in full, they are what a bug report needs
            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                wrapMode: Text.Wrap
                color: Theme.errorColor
                font.pixelSize: Theme.fontSizeSmall
                visible: text !== ""
                text: app.engineError !== "" ? qsTr("The engine did not start: %1").arg(app.engineError)
                      : app.st.sodium !== "" ? qsTr("Encryption is not available: %1").arg(app.st.sodium)
                      : ""
            }

            // ---- not signed in
            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                wrapMode: Text.Wrap
                color: Theme.highlightColor
                visible: app.ready && !app.st.signedIn
                text: (app.st.error !== "" ? app.st.error + "\n\n" : "")
                      + qsTr("Sign in to your Ente account to upload photos from this phone. "
                             + "They are encrypted here before they are sent.")
            }
            Button {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: app.ready && !app.st.signedIn
                enabled: app.st.sodium === ""
                text: qsTr("Sign in")
                onClicked: pageStack.push(Qt.resolvedUrl("LoginPage.qml"), { app: page.app })
            }

            // ---- signed in
            Button {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: app.st.signedIn
                text: qsTr("Browse photos in Ente")
                onClicked: pageStack.push(Qt.resolvedUrl("CloudPage.qml"), { app: page.app })
            }
            ValueButton {
                visible: app.st.signedIn
                label: qsTr("Main album")
                value: app.st.albumName !== "" ? app.st.albumName : qsTr("Choose")
                onClicked: pageStack.push(Qt.resolvedUrl("AlbumsPage.qml"), { app: page.app })
            }
            ValueButton {
                visible: app.st.signedIn
                label: qsTr("Sources")
                value: app.st.sources === 0 ? qsTr("none switched on")
                                            : qsTr("%n folder(s)", "", app.st.sources)
                onClicked: pageStack.push(Qt.resolvedUrl("SourcesPage.qml"), { app: page.app })
            }
            ButtonLayout {
                visible: app.st.signedIn
                Button {
                    enabled: page.canUpload
                    text: qsTr("Choose photos")
                    onClicked: pageStack.push(photoPicker)
                }
                Button {
                    enabled: page.canUpload
                    text: qsTr("Choose videos")
                    onClicked: pageStack.push(videoPicker)
                }
                Button {
                    ButtonLayout.newLine: true
                    enabled: app.st.signedIn && app.st.sources > 0
                    text: qsTr("Upload new from sources")
                    onClicked: {
                        page.notice = ""
                        app.uploadAll(function (text) { page.notice = text })
                    }
                }
            }
            TextSwitch {
                visible: app.st.signedIn
                text: qsTr("Videos from sources too")
                description: qsTr("Videos can be large; photos alone are quicker on mobile data")
                checked: app.includeVideos
                onCheckedChanged: app.includeVideos = checked
            }
            TextSwitch {
                visible: app.st.signedIn
                automaticCheck: false
                checked: app.st.auto.on
                text: qsTr("Automatic backup")
                description: !app.st.auto.on
                    ? qsTr("Every 15 minutes, new photos from the sources go to Ente, also "
                           + "while the app is closed")
                    : page.autoLastText()
                onClicked: app.callState("set_auto", [{ "on": !checked }])
            }
            TextSwitch {
                visible: app.st.signedIn && app.st.auto.on
                automaticCheck: false
                checked: app.st.auto.wifiOnly
                text: qsTr("Only on Wi-Fi")
                onClicked: app.callState("set_auto", [{ "wifiOnly": !checked }])
            }

            // ---- space left: in Ente and on the phone (tap to look again)
            SectionHeader {
                visible: app.ready
                text: qsTr("Space")
            }
            BackgroundItem {
                id: spaceArea
                visible: app.ready
                width: parent.width
                height: spaceColumn.height + Theme.paddingMedium
                onClicked: app.refreshSpace(true)

                readonly property var ente: app.space.ente || {}
                readonly property var phone: app.space.phone || {}
                readonly property bool known: ente.total !== undefined && ente.total > 0
                // almost full: less than 2 % or 1 GB left
                readonly property bool tight: known && (ente.free < ente.total * 0.02
                                                        || ente.free < 1024 * 1024 * 1024)

                Column {
                    id: spaceColumn
                    width: parent.width
                    ProgressBar {
                        width: parent.width
                        visible: app.st.signedIn
                        minimumValue: 0
                        maximumValue: spaceArea.known ? spaceArea.ente.total : 1
                        value: spaceArea.known ? spaceArea.ente.used : 0
                        indeterminate: !spaceArea.known && spaceArea.ente.error === undefined
                        label: spaceArea.known
                               ? qsTr("Ente: %1 free of %2").arg(app.bytes(spaceArea.ente.free))
                                                           .arg(app.bytes(spaceArea.ente.total))
                               : spaceArea.ente.error !== undefined ? spaceArea.ente.error
                               : qsTr("Ente: asking")
                        valueText: spaceArea.known
                                   ? Math.round(100 * spaceArea.ente.used / spaceArea.ente.total) + " %"
                                   : ""
                    }
                    Label {
                        x: Theme.horizontalPageMargin
                        width: parent.width - 2 * x
                        visible: text !== ""
                        wrapMode: Text.Wrap
                        font.pixelSize: Theme.fontSizeExtraSmall
                        color: spaceArea.tight ? Theme.errorColor : Theme.secondaryHighlightColor
                        text: !spaceArea.known || !app.st.signedIn ? ""
                              : (spaceArea.tight ? qsTr("Almost full: uploads will soon be refused.")
                                                   + " " : "")
                                + qsTr("%1 used by %n file(s)", "", spaceArea.ente.files)
                                      .arg(app.bytes(spaceArea.ente.used))
                                + (spaceArea.ente.family ? " · " + qsTr("family plan") : "")
                                + (spaceArea.ente.expires > 0
                                   ? " · " + qsTr("plan until %1").arg(Qt.formatDate(
                                         new Date(spaceArea.ente.expires * 1000), "d MMM yyyy"))
                                   : "")
                    }
                    Label {
                        x: Theme.horizontalPageMargin
                        width: parent.width - 2 * x
                        visible: spaceArea.phone.free !== undefined
                        wrapMode: Text.Wrap
                        font.pixelSize: Theme.fontSizeSmall
                        color: Theme.highlightColor
                        text: qsTr("Phone: %1 free of %2").arg(app.bytes(spaceArea.phone.free))
                                                         .arg(app.bytes(spaceArea.phone.total))
                    }
                }
            }
            Button {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: app.st.signedIn
                enabled: !app.up.active
                text: qsTr("Free up space on the phone")
                onClicked: pageStack.push(Qt.resolvedUrl("FreeUpPage.qml"), { app: page.app })
            }

            // ---- progress
            ProgressBar {
                width: parent.width
                visible: app.up.active
                minimumValue: 0
                maximumValue: 1
                value: app.up.fraction
                indeterminate: app.up.stage !== "upload"
                label: app.uploadText
                valueText: qsTr("%1 of %2").arg(Math.min(app.handled + 1, app.up.total))
                                           .arg(app.up.total)
            }
            Button {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: app.up.active
                text: qsTr("Stop")
                onClicked: app.call("cancel")
            }
            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
                color: Theme.secondaryHighlightColor
                visible: text !== ""
                text: app.uploadError !== "" ? app.uploadError
                      : page.notice !== "" ? page.notice
                      : (!app.up.active && app.up.total > 0)
                        ? qsTr("%1 uploaded, %2 skipped, %3 failed")
                              .arg(app.up.done).arg(app.up.skipped).arg(app.up.failed)
                          + (app.up.done > 0
                             ? "\n" + qsTr("In Ente they are in the album \"%1\", and in the timeline at the date each was taken, not at the top.").arg(app.st.albumName)
                             : "")
                      : (app.st.signedIn && app.st.albumId === 0)
                        ? qsTr("Choose the album the photos go to")
                      : ""
            }

            SectionHeader {
                visible: view.count > 0
                text: qsTr("This session")
            }
        }

        delegate: PhotoRow {
            path: model.path
            name: model.name
            video: model.video
            taken: model.taken
            status: model.status
            detail: model.detail
        }

        VerticalScrollDecorator { }
    }
}
