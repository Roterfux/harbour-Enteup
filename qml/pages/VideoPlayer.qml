// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
// Plays one video file with sound (taken from Postpeek, where it plays with sound on the
// phone). Tap: play / pause (again after the end: from the start); tap on the time bar:
// jump there; the speaker button switches the sound.
import QtQuick 2.6
import Sailfish.Silica 1.0
import QtMultimedia 5.6
import Nemo.KeepAlive 1.2

Item {
    id: player

    property url video
    property bool autoStart: true
    property bool muted: false
    property bool wanted: autoStart   // the user wants it running
    // a frame is on screen (until then the caller shows the still picture)
    readonly property bool showing: media.error === MediaPlayer.NoError && media.position > 0
    readonly property bool ended: media.status === MediaPlayer.EndOfMedia

    // What the phone's player makes of the file, in one line: shown when it fails, so a
    // video that does not play can be reported precisely
    readonly property string report: {
        var parts = []
        if (media.error !== MediaPlayer.NoError)
            parts.push("error " + media.error + ": " + media.errorString)
        parts.push("status " + media.status)
        parts.push("picture " + (media.hasVideo ? "yes" : "no"))
        parts.push("sound " + (media.hasAudio ? "yes" : "no"))
        var meta = media.metaData
        if (meta && meta.videoCodec) parts.push(String(meta.videoCodec))
        if (meta && meta.audioCodec) parts.push(String(meta.audioCodec))
        return parts.join(" · ")
    }

    function clock(ms) {
        var s = Math.floor(ms / 1000)
        var m = Math.floor(s / 60)
        s = s % 60
        return m + ":" + (s < 10 ? "0" : "") + s
    }

    // The screen stays on while a video runs
    DisplayBlanking {
        preventBlanking: media.playbackState === MediaPlayer.PlayingState
    }

    Video {
        id: media
        anchors.fill: parent
        source: player.video
        autoPlay: player.autoStart
        muted: player.muted
        fillMode: VideoOutput.PreserveAspectFit
        onStopped: if (status === MediaPlayer.EndOfMedia) player.wanted = false
    }

    BusyIndicator {
        id: busy
        anchors.centerIn: parent
        size: BusyIndicatorSize.Medium
        running: media.error === MediaPlayer.NoError && player.wanted
                 && (media.status === MediaPlayer.Loading || media.status === MediaPlayer.Buffering
                     || media.status === MediaPlayer.Stalled)
    }

    // The play button, whenever the video does not run
    Rectangle {
        anchors.centerIn: parent
        visible: !busy.running && media.error === MediaPlayer.NoError
                 && media.playbackState !== MediaPlayer.PlayingState
        width: Theme.itemSizeLarge
        height: width
        radius: width / 2
        color: Theme.rgba("black", 0.6)
        Image {
            anchors.centerIn: parent
            source: "image://theme/icon-l-play?" + "white"
        }
    }

    Rectangle {
        anchors.fill: errorText
        anchors.margins: -Theme.paddingMedium
        visible: errorText.visible
        color: Theme.rgba("black", 0.6)
    }
    Label {
        id: errorText
        anchors.centerIn: parent
        width: parent.width - 2 * Theme.horizontalPageMargin
        visible: media.error !== MediaPlayer.NoError
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        font.pixelSize: Theme.fontSizeExtraSmall
        color: "white"
        text: qsTr("The phone cannot play this video.") + "\n" + player.report
    }

    MouseArea {
        anchors.fill: parent
        enabled: media.error === MediaPlayer.NoError
        onClicked: {
            if (mouse.y > height - Theme.itemSizeSmall && media.duration > 0
                    && media.position > 0) {
                media.seek(media.duration * mouse.x / width)        // tap on the time bar
            } else if (media.playbackState === MediaPlayer.PlayingState) {
                player.wanted = false
                media.pause()
            } else {
                player.wanted = true
                media.play()
            }
        }
    }

    // Sound on / off
    Rectangle {
        anchors { right: parent.right; bottom: parent.bottom
                  margins: Theme.paddingMedium; bottomMargin: Theme.itemSizeSmall }
        width: Theme.itemSizeSmall
        height: width
        radius: width / 2
        color: Theme.rgba("black", 0.5)
        visible: media.hasAudio
        Image {
            anchors.centerIn: parent
            source: (player.muted ? "image://theme/icon-m-speaker-mute"
                                  : "image://theme/icon-m-speaker") + "?white"
        }
        MouseArea {
            anchors.fill: parent
            onClicked: player.muted = !player.muted
        }
    }

    // Time bar along the bottom
    Rectangle {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: Theme.paddingSmall
        color: Theme.rgba("white", 0.25)
        visible: media.duration > 0
        Rectangle {
            height: parent.height
            width: parent.width * Math.min(1, media.position / media.duration)
            color: "white"
        }
    }
    Label {
        anchors { left: parent.left; bottom: parent.bottom
                  margins: Theme.paddingMedium; bottomMargin: Theme.paddingLarge }
        visible: media.duration > 0
        font.pixelSize: Theme.fontSizeExtraSmall
        color: "white"
        text: player.clock(media.position) + " / " + player.clock(media.duration)
    }
}
