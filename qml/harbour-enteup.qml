// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0
import Nemo.Configuration 1.0
import io.thp.pyotherside 1.3
import "pages"

ApplicationWindow {
    // Not "app": the pages have a property of that name, and inside the initialPage
    // component "app: app" would bind the page's property to itself (undefined).
    id: appWindow

    property bool ready: false          // the engine is loaded
    property string engineError: ""     // why the engine could not start
    // The engine's state: signedIn, email, server, albumId, albumName, uploaded, sodium, error
    property var st: ({ signedIn: false, email: "", server: "", albumId: 0, albumName: "",
                        uploaded: 0, sources: 0, sodium: "", error: "",
                        auto: { on: false, wifiOnly: true, videos: true }, autoLast: {} })
    // The upload queue: active, total, done, skipped, failed, name, stage, fraction, error
    property var up: ({ active: false, total: 0, done: 0, skipped: 0, failed: 0, name: "",
                        stage: "", fraction: 0, error: "" })
    property string uploadError: ""     // why the last batch ended early
    property alias results: resultModel // finished files of this session, newest first
    property alias includeVideos: cfg.includeVideos

    readonly property int handled: up.done + up.skipped + up.failed
    readonly property string uploadText: !up.active ? ""
        : up.stage === "index" ? qsTr("Checking what is in Ente already")
        : up.stage === "wait" ? qsTr("Waiting for the automatic backup to finish")
        : up.stage === "upload" ? qsTr("Uploading %1").arg(up.name)
        : qsTr("Checking %1").arg(up.name)

    // the automatic backup (another process) reads its settings from the engine's files
    onIncludeVideosChanged: if (ready) call("set_auto", [{ "videos": includeVideos }])
    onReadyChanged: if (ready) call("set_auto", [{ "videos": includeVideos }],
                                    function (state) { appWindow.st = state })

    // back in the foreground: the automatic backup may have run meanwhile
    Connections {
        target: Qt.application
        onStateChanged: if (Qt.application.state === Qt.ApplicationActive && appWindow.ready) {
            appWindow.callState("state")   // and the pending count with it
            appWindow.refreshSpace(false)
        }
    }

    signal loginStep(string step, string message)
    signal albumsLoaded(var list, string message, var made)

    // ---- browsing the account
    // syncing, albums, albumsDone, fetched, files, synced (epoch s), error
    property var cloud: ({ syncing: false, albums: 0, albumsDone: 0, fetched: 0, files: 0,
                           synced: 0, error: "", locked: 0 })
    property bool cloudStarted: false   // a sync was started since the app opened
    signal cloudSynced()
    signal thumbReady(real thumbId, string thumbPath)
    signal fullReady(real fullId, var result)
    signal savedReady(real savedId, var result)
    signal trashedReady(real trashedId, var result)
    // an action on the account ended: {what, done, skipped, error, ...}
    signal actedReady(var result)
    // freeing up the phone: step 0 = the check ({progress, total} while it runs, then
    // {files, bytes, checked, notInEnte, error}), 1 = deleting ({done, freed, kept, error})
    signal cleanupReady(int step, var result)

    // files in the switched-on sources not backed up yet (for the cover); -1 = not known
    property int pending: -1
    function refreshPending() {
        if (!ready || !st.signedIn) { pending = -1; return }
        call("pending", [includeVideos], function (count) { appWindow.pending = count })
    }
    // Space left: {ente: {used, total, free, files, expires, family} or {error},
    //             phone: {free, total}}
    property var space: ({ ente: {}, phone: {} })
    property real spaceAt: 0            // when it was last asked (ms)
    function refreshSpace(force) {
        if (!ready) return
        var now = Date.now()
        if (!force && now - spaceAt < 60000) return
        spaceAt = now
        call("space", [], function (found) { appWindow.space = found })
    }

    // Sizes as Ente's apps show them (1 GB = 1024 MB)
    function bytes(n) {
        if (n === undefined || n === null) return ""
        var units = ["B", "KB", "MB", "GB", "TB"]
        var i = 0
        while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
        return (i >= 3 ? n.toFixed(1) : Math.round(n)) + " " + units[i]
    }

    // when the last backup (here or automatic) brought something to Ente
    readonly property real lastBackup: {
        var last = st.lastUpload || 0
        var auto = st.autoLast || {}
        if (auto.ran && auto.uploaded > 0 && auto.at > last) last = auto.at
        return last
    }
    function lastBackupText() {
        if (!lastBackup) return ""
        var when = new Date(lastBackup * 1000)
        var today = new Date()
        return when.toDateString() === today.toDateString()
               ? Qt.formatTime(when, "hh:mm") : Qt.formatDate(when, "d MMM")
    }
    // a photo left the account here: grids load again. Not "cloudChanged": QML makes that
    // signal for the property "cloud" itself, and a second one stops the window loading
    signal photosRemoved()

    // Share a file through the system's share menu
    property var fileShare: null
    function shareFile(path) {
        var ext = path.substring(path.lastIndexOf(".") + 1).toLowerCase()
        var types = { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", gif: "image/gif",
                      webp: "image/webp", heic: "image/heic", heif: "image/heif",
                      mp4: "video/mp4", m4v: "video/mp4", mov: "video/quicktime",
                      "3gp": "video/3gpp", mkv: "video/x-matroska", webm: "video/webm",
                      avi: "video/x-msvideo" }
        if (fileShare === null) {
            try {
                fileShare = Qt.createQmlObject('import Sailfish.Share 1.0; ShareAction { }',
                                               appWindow, "fileshare")
            } catch (error) {
                console.log("No share menu:", error)
                fileShare = false
            }
        }
        if (!fileShare) return false
        fileShare.title = qsTr("Share")
        fileShare.mimeType = types[ext] || "application/octet-stream"
        fileShare.resources = ["file://" + encodeURI(path).replace(/#/g, "%23")
                                                          .replace(/\?/g, "%3F")]
        fileShare.trigger()
        return true
    }

    // file id -> thumbnail path; null while it is being fetched, "" when that failed
    property var thumbCache: ({})
    property var thumbWanted: []

    // signed out: the engine dropped the account's thumbnails, so the paths are gone too
    // (one handler only: a second onStChanged would stop the window from loading)
    onStChanged: {
        if (!st.signedIn) {
            thumbCache = ({})
            cloudStarted = false
            if (space.ente && space.ente.total !== undefined) {
                space = ({ ente: {}, phone: space.phone })
                spaceAt = 0
            }
        } else if (!space.ente || space.ente.total === undefined) {
            refreshSpace(false)             // signed in, nothing known yet
        }
        refreshPending()
    }

    function cloudSync() {
        cloudStarted = true
        call("cloud_sync")
    }

    // The thumbnail path when it is here; otherwise it is asked for and thumbReady follows
    function thumb(fileId) {
        var known = thumbCache[fileId]
        if (known !== undefined) return known
        thumbCache[fileId] = null
        thumbWanted.push(fileId)
        if (!thumbTimer.running) thumbTimer.start()
        return null
    }

    function retryThumbs() {
        for (var key in thumbCache) if (thumbCache[key] === "") delete thumbCache[key]
        call("cloud_retry_thumbs")
    }

    Timer {
        id: thumbTimer
        interval: 60            // collect the cells that appear together into one call
        onTriggered: {
            var ids = appWindow.thumbWanted
            appWindow.thumbWanted = []
            appWindow.call("cloud_thumbs", [ids], function (ready) {
                for (var key in ready) {
                    appWindow.thumbCache[key] = ready[key]
                    appWindow.thumbReady(parseInt(key), ready[key])
                }
            })
        }
    }

    // Everything new from the switched-on sources, each into its album; report(text) gets
    // a line for the user, "" when files were queued and nothing was left out
    function uploadAll(report) {
        uploadError = ""
        call("upload_sources", [includeVideos, ""], function (result) {
            var text = ""
            if (result.noAlbum.length > 0)
                text = qsTr("Left out, no album chosen: %1").arg(result.noAlbum.join(", "))
            else if (result.queued === 0)
                text = qsTr("Nothing new in the sources")
            report(text)
        })
    }

    // Call a function of python/backend.py; the callback gets its result
    function call(name, args, callback) {
        py.call("backend." + name, args || [], callback || function () {})
    }

    // For the quick calls that answer with the new state
    function callState(name, args) {
        call(name, args, function (state) { appWindow.st = state })
    }

    function uploadFiles(paths) {
        if (paths.length === 0) return
        uploadError = ""
        call("upload", [paths])
    }

    ListModel { id: resultModel }

    ConfigurationGroup {
        id: cfg
        path: "/apps/harbour-enteup"
        property bool includeVideos: true
    }

    // Keeps the phone from sleeping in the middle of an upload, where the system has it
    property var keepAlive: null
    Component.onCompleted: {
        try {
            keepAlive = Qt.createQmlObject(
                'import Nemo.KeepAlive 1.2; KeepAlive { }', appWindow, "keepalive")
        } catch (error) {
            console.log("No KeepAlive:", error)
        }
    }
    Binding {
        when: appWindow.keepAlive !== null
        target: appWindow.keepAlive
        property: "enabled"
        value: appWindow.up.active
    }

    Python {
        id: py
        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl("../python"))
            setHandler("state", function (state) { appWindow.st = state })
            setHandler("login", function (step, message) {
                appWindow.loginStep(step, message)
            })
            setHandler("albums", function (list, message, made) {
                appWindow.albumsLoaded(list, message, made || 0)
            })
            setHandler("upload", function (progress) {
                if (progress.error) appWindow.uploadError = progress.error
                var ended = appWindow.up.active && !progress.active
                appWindow.up = progress
                if (ended) {
                    appWindow.refreshPending()
                    appWindow.refreshSpace(true)
                }
            })
            setHandler("cloud", function (status) {
                var was = appWindow.cloud.syncing
                appWindow.cloud = status
                if (was && !status.syncing) appWindow.cloudSynced()
            })
            setHandler("thumb", function (fileId, path) {
                appWindow.thumbCache[fileId] = path
                appWindow.thumbReady(fileId, path)
            })
            setHandler("full", function (fileId, result) {
                appWindow.fullReady(fileId, result)
            })
            setHandler("saved", function (fileId, result) {
                appWindow.savedReady(fileId, result)
            })
            setHandler("acted", function (zero, result) {
                if (result.done > 0 && result.what !== "save" && result.what !== "trashsync")
                    appWindow.photosRemoved()       // grids load again
                // deleted for good: the space comes back
                if (result.done > 0 && (result.what === "delete" || result.what === "empty"))
                    appWindow.refreshSpace(true)
                appWindow.actedReady(result)
            })
            setHandler("cleanup", function (step, result) {
                if (step === 1 && result.done > 0) {
                    appWindow.refreshSpace(true)
                    appWindow.refreshPending()
                }
                appWindow.cleanupReady(step, result)
            })
            setHandler("trashed", function (fileId, result) {
                // fileId 0: several at once (result.done of them went)
                if (!result.error || result.done > 0) appWindow.photosRemoved()
                appWindow.trashedReady(fileId, result)
            })
            setHandler("result", function (result) {
                resultModel.insert(0, result)
                if (resultModel.count > 200) resultModel.remove(200, resultModel.count - 200)
            })
            importModule("backend", function () {
                py.call("backend.init", [], function (state) {
                    appWindow.st = state
                    appWindow.ready = true
                    appWindow.refreshSpace(true)
                    // signed in: start in the photo browser; back leads to the main page
                    if (state.signedIn && pageStack.depth === 1)
                        pageStack.push(Qt.resolvedUrl("pages/CloudPage.qml"), { app: appWindow },
                                       PageStackAction.Immediate)
                })
            })
        }
        onError: {
            console.log("Python:", traceback)
            appWindow.engineError = traceback
        }
    }

    allowedOrientations: defaultAllowedOrientations
    initialPage: Component { MainPage { app: appWindow } }

    // Cover: the backup at a glance - running, or how many files wait, and the last backup
    cover: Component {
        CoverBackground {
            // the count is looked at again whenever the cover shows (files may be new)
            onStatusChanged: if (status === Cover.Active) appWindow.refreshPending()

            Column {
                anchors { left: parent.left; right: parent.right; margins: Theme.paddingLarge
                          verticalCenter: parent.verticalCenter
                          verticalCenterOffset: -Theme.paddingLarge }
                spacing: Theme.paddingSmall
                Label {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: Theme.fontSizeHuge
                    color: Theme.highlightColor
                    text: !appWindow.st.signedIn ? "–"
                          : appWindow.up.active ? appWindow.handled + "/" + appWindow.up.total
                          : appWindow.pending >= 0 ? appWindow.pending : "…"
                }
                Label {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    color: Theme.secondaryHighlightColor
                    text: !appWindow.st.signedIn ? qsTr("Not signed in")
                          : appWindow.up.active ? qsTr("uploading")
                          : appWindow.pending === 0 ? qsTr("all backed up")
                          : qsTr("new to back up")
                }
                Label {
                    width: parent.width
                    visible: text !== "" && !appWindow.up.active
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    font.pixelSize: Theme.fontSizeExtraSmall
                    color: Theme.secondaryColor
                    text: appWindow.lastBackup > 0
                          ? qsTr("Last backup %1").arg(appWindow.lastBackupText()) : ""
                }
                Label {
                    width: parent.width
                    visible: appWindow.st.signedIn && appWindow.st.auto.on && !appWindow.up.active
                    horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: Theme.fontSizeExtraSmall
                    color: Theme.secondaryColor
                    text: qsTr("Automatic backup on")
                }
            }
            CoverActionList {
                enabled: appWindow.st.signedIn && !appWindow.up.active && appWindow.st.sources > 0
                CoverAction {
                    iconSource: "image://theme/icon-cover-sync"
                    onTriggered: appWindow.uploadAll(function (text) {})
                }
            }
        }
    }
}
