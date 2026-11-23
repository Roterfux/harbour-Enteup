// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0

// An icon with a small label under it, for the selection bar of the photo grid
BackgroundItem {
    id: button
    property string icon
    property string label

    // up to six side by side on a phone held upright
    width: Math.min(Theme.itemSizeMedium + Theme.paddingSmall, Screen.width / 6.2)
    height: Theme.itemSizeLarge

    Column {
        anchors.centerIn: parent
        Icon {
            anchors.horizontalCenter: parent.horizontalCenter
            source: button.icon
            highlighted: button.highlighted
        }
        Label {
            anchors.horizontalCenter: parent.horizontalCenter
            width: button.width
            horizontalAlignment: Text.AlignHCenter
            truncationMode: TruncationMode.Fade
            font.pixelSize: Theme.fontSizeTiny
            color: button.highlighted ? Theme.highlightColor : Theme.primaryColor
            text: button.label
        }
    }
}
