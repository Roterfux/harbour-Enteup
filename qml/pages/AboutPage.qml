// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0

Page {
    id: page
    property var app
    // Stamped with the package version when the RPM is built
    readonly property string appVersion: "dev"
    allowedOrientations: Orientation.All

    SilicaFlickable {
        anchors.fill: parent
        contentHeight: column.height + Theme.paddingLarge

        Column {
            id: column
            width: parent.width
            spacing: Theme.paddingMedium

            PageHeader { title: qsTr("About") }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                wrapMode: Text.Wrap
                color: Theme.highlightColor
                text: qsTr("Enteup %1\n\nUploads photos and videos from this phone to an Ente Photos account. "
                           + "Files, thumbnails, names and dates are encrypted on the phone, the way "
                           + "Ente's own apps do it.\n\nAn independent app: not made or supported by Ente.")
                          .arg(page.appVersion)
            }

            SectionHeader { text: qsTr("Status") }
            DetailItem { label: qsTr("Account"); value: app.st.signedIn ? app.st.email : qsTr("Not signed in") }
            DetailItem { label: qsTr("Server"); value: app.st.server }
            DetailItem { label: qsTr("Album"); value: app.st.albumName !== "" ? app.st.albumName : "-" }
            DetailItem { label: qsTr("Files uploaded"); value: app.st.uploaded }
            DetailItem {
                label: qsTr("Encryption")
                value: app.st.sodium === "" ? qsTr("ready") : app.st.sodium
            }
            DetailItem {
                visible: app.st.error !== ""
                label: qsTr("Last problem")
                value: app.st.error
            }
            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                wrapMode: Text.Wrap
                visible: app.engineError !== ""
                font.pixelSize: Theme.fontSizeExtraSmall
                color: Theme.errorColor
                text: app.engineError
            }

            SectionHeader { text: qsTr("Uploaded files") }
            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                wrapMode: Text.Wrap
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.secondaryHighlightColor
                text: qsTr("The app remembers on this phone which files it has uploaded and skips "
                           + "them. It does not look at what is already in the account, so photos "
                           + "uploaded by another app would be uploaded again.\n\nEmpty and broken "
                           + "files (pictures cut off, videos whose recording did not finish) are "
                           + "left out until they change. Forgetting tries them again too.")
            }
            Button {
                anchors.horizontalCenter: parent.horizontalCenter
                enabled: app.st.uploaded > 0 && !app.up.active
                text: qsTr("Forget uploaded files")
                onClicked: remorse.execute(qsTr("Forgetting"),
                                           function () { app.callState("forget_uploads") })
            }
        }
        VerticalScrollDecorator { }
    }
    RemorsePopup { id: remorse }
}
