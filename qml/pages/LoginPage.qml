// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0

// Sign in: e-mail address -> the code Ente mails -> (authenticator code) -> password.
// The password only unlocks the account's keys on the phone; it is neither sent nor kept.
Page {
    id: page
    property var app
    allowedOrientations: Orientation.All

    property string step: "email"       // email | code | totp | password
    property bool busy: false
    property string problem: ""

    function submit() {
        if (busy) return
        problem = ""
        busy = true
        if (step === "email") app.call("send_code", [serverField.text, emailField.text])
        else if (step === "code") app.call("verify_code", [codeField.text])
        else if (step === "totp") app.call("verify_totp", [totpField.text])
        else app.call("unlock", [passwordField.text])
    }

    Connections {
        target: page.app
        onLoginStep: {
            page.busy = false
            if (step === "error") {
                page.problem = message
            } else if (step === "done") {
                passwordField.text = ""
                pageStack.pop()
            } else {
                page.step = step
                if (step === "code") codeField.forceActiveFocus()
                else if (step === "totp") totpField.forceActiveFocus()
                else if (step === "password") passwordField.forceActiveFocus()
            }
        }
    }

    SilicaFlickable {
        anchors.fill: parent
        contentHeight: column.height + Theme.paddingLarge

        PullDownMenu {
            visible: page.step !== "email"
            MenuItem {
                text: qsTr("Start again")
                onClicked: { page.step = "email"; page.problem = ""; page.busy = false }
            }
        }

        Column {
            id: column
            width: parent.width
            spacing: Theme.paddingMedium

            PageHeader { title: qsTr("Sign in to Ente") }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                wrapMode: Text.Wrap
                color: Theme.highlightColor
                text: page.step === "email" ? qsTr("Ente mails a code to your account's address.")
                      : page.step === "code" ? qsTr("Enter the code from the mail sent to %1.").arg(emailField.text)
                      : page.step === "totp" ? qsTr("Enter the code from your authenticator app.")
                      : qsTr("Enter your Ente password. It unlocks your keys on this phone; it is not sent anywhere and not kept.")
            }

            TextField {
                id: emailField
                visible: page.step === "email"
                label: qsTr("E-mail address")
                placeholderText: label
                inputMethodHints: Qt.ImhEmailCharactersOnly | Qt.ImhNoAutoUppercase | Qt.ImhNoPredictiveText
                EnterKey.enabled: text.indexOf("@") > 0
                EnterKey.iconSource: "image://theme/icon-m-enter-accept"
                EnterKey.onClicked: page.submit()
            }
            TextField {
                id: serverField
                visible: page.step === "email"
                label: qsTr("Server (change only for your own)")
                text: "https://api.ente.io"
                inputMethodHints: Qt.ImhUrlCharactersOnly | Qt.ImhNoAutoUppercase | Qt.ImhNoPredictiveText
            }
            TextField {
                id: codeField
                visible: page.step === "code"
                label: qsTr("Code from the mail")
                placeholderText: label
                inputMethodHints: Qt.ImhDigitsOnly
                EnterKey.enabled: text.length >= 6
                EnterKey.iconSource: "image://theme/icon-m-enter-accept"
                EnterKey.onClicked: page.submit()
            }
            TextField {
                id: totpField
                visible: page.step === "totp"
                label: qsTr("Authenticator code")
                placeholderText: label
                inputMethodHints: Qt.ImhDigitsOnly
                EnterKey.enabled: text.length >= 6
                EnterKey.iconSource: "image://theme/icon-m-enter-accept"
                EnterKey.onClicked: page.submit()
            }
            PasswordField {
                id: passwordField
                visible: page.step === "password"
                label: qsTr("Password")
                EnterKey.enabled: text.length > 0
                EnterKey.iconSource: "image://theme/icon-m-enter-accept"
                EnterKey.onClicked: page.submit()
            }

            Button {
                anchors.horizontalCenter: parent.horizontalCenter
                enabled: !page.busy
                text: page.step === "email" ? qsTr("Send code")
                      : page.step === "password" ? qsTr("Unlock") : qsTr("Continue")
                onClicked: page.submit()
            }
            BusyIndicator {
                anchors.horizontalCenter: parent.horizontalCenter
                size: BusyIndicatorSize.Medium
                running: page.busy
                visible: running
            }
            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
                color: Theme.secondaryHighlightColor
                font.pixelSize: Theme.fontSizeSmall
                visible: page.busy && page.step === "password"
                text: qsTr("Unlocking the keys takes a few seconds")
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
        VerticalScrollDecorator { }
    }
}
