// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
// A video playing by itself in a big grid tile: no sound, over and over. Loaded through a
// Loader, so a phone without the multimedia module only misses this.
import QtQuick 2.6
import QtMultimedia 5.6

Item {
    id: clip
    property url video

    MediaPlayer {
        id: media
        source: clip.video
        autoPlay: true
        muted: true
        loops: MediaPlayer.Infinite
    }
    VideoOutput {
        anchors.fill: parent
        source: media
        fillMode: VideoOutput.PreserveAspectCrop
        // the thumbnail stays until the first frame is there
        opacity: media.position > 0 ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 200 } }
    }
}
