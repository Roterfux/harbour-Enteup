%{!?_userunitdir:%global _userunitdir /usr/lib/systemd/user}
Name:       harbour-enteup
Summary:    Upload photos to Ente
Version:    0.11.0
Release:    5
License:    GPLv3+
BuildArch:  noarch
Source0:    %{name}-%{version}.tar.bz2

# UI
Requires:   sailfishsilica-qt5 >= 1.1.0
Requires:   mapplauncherd-booster-silica-qt5
# Engine: Python, driven through PyOtherSide
Requires:   pyotherside-qml-plugin-python3-qt5
Requires:   python3-base
Requires:   python3-sqlite
# Thumbnails and EXIF
Requires:   python3-imaging
# Encryption (called through ctypes)
Requires:   libsodium
# Automatic backup: a user timer
Requires:   systemd

%description
Enteup uploads photos and videos from the phone to an Ente Photos account.
Everything is encrypted on the phone before it is sent, the way Ente's own
apps do it. An independent app, not made or supported by Ente.

%prep
%setup -q -n %{name}-%{version}
# Sources may come from a filesystem without Unix modes (e.g. a Windows drive)
find . -type f -exec chmod 0644 {} +
# ... and with Windows line endings, which break the .desktop file
find . -type f \( -name '*.desktop' -o -name '*.qml' -o -name '*.py' -o -name '*.service' \
    -o -name '*.timer' \) -exec sed -i 's/\r$//' {} +

%build
# Nothing to build: QML UI + Python engine

%install
rm -rf %{buildroot}
install -d %{buildroot}%{_datadir}/%{name}
cp -r qml python %{buildroot}%{_datadir}/%{name}/
find %{buildroot}%{_datadir}/%{name} -name __pycache__ -prune -exec rm -rf {} \;
# Stamp the exact package version into the About page (fail if the marker moved)
sed -i 's/appVersion: "[^"]*"/appVersion: "%{version}-%{release}"/' \
    %{buildroot}%{_datadir}/%{name}/qml/pages/AboutPage.qml
grep -q 'appVersion: "%{version}-%{release}"' %{buildroot}%{_datadir}/%{name}/qml/pages/AboutPage.qml
install -D -m 0644 harbour-enteup.desktop %{buildroot}%{_datadir}/applications/%{name}.desktop
for size in 86x86 108x108 128x128 172x172; do
    install -D -m 0644 icons/$size/%{name}.png %{buildroot}%{_datadir}/icons/hicolor/$size/apps/%{name}.png
done
# Automatic backup: runs every 15 minutes, does nothing while it is off in the app
install -D -m 0644 data/%{name}-backup.service %{buildroot}%{_userunitdir}/%{name}-backup.service
install -D -m 0644 data/%{name}-backup.timer %{buildroot}%{_userunitdir}/%{name}-backup.timer
install -d %{buildroot}%{_userunitdir}/timers.target.wants
ln -s ../%{name}-backup.timer %{buildroot}%{_userunitdir}/timers.target.wants/%{name}-backup.timer

%post
# Start the timer in the running session at once (otherwise after the next restart)
if [ "$1" -ge 1 ]; then
    systemctl-user daemon-reload >/dev/null 2>&1 || :
    systemctl-user start %{name}-backup.timer >/dev/null 2>&1 || :
fi

%preun
if [ "$1" -eq 0 ]; then
    systemctl-user stop %{name}-backup.timer %{name}-backup.service >/dev/null 2>&1 || :
fi

%postun
systemctl-user daemon-reload >/dev/null 2>&1 || :

%files
%defattr(-,root,root,-)
%license LICENSE
%{_datadir}/%{name}
%{_datadir}/applications/%{name}.desktop
%{_datadir}/icons/hicolor/*/apps/%{name}.png
%{_userunitdir}/%{name}-backup.service
%{_userunitdir}/%{name}-backup.timer
%{_userunitdir}/timers.target.wants/%{name}-backup.timer

%changelog
* Mon Nov 23 2026 Enteup contributors - 0.11.0-5
- The license text (GPL-3.0-or-later) is installed with the package

* Fri Nov 20 2026 Enteup contributors - 0.11.0-4
- New app icon: a photo with an upload badge (browsing and backing up)

* Fri Nov 20 2026 Enteup contributors - 0.11.0-3
- The app starts with the photo browser when signed in; swipe back for the main page

* Fri Nov 20 2026 Enteup contributors - 0.11.0-2
- Memories as title pictures at the top of the photo grid: one picture per memory (a
  favorite when there is one) with its title ("1 month ago", "Best of November 2023");
  swipe sideways through them, tap one to see its photos

* Fri Nov 20 2026 Enteup contributors - 0.11.0-1
- Free up space on the phone (main page): photos and videos in the upload sources older
  than a chosen age (1 month, 3 months, 1 year, any) whose exact content is in one of your
  albums in Ente. The list comes first (tap one to keep it); each file is checked again
  just before it is deleted
- Duplicates (pull-down menu of the grid): photos stored more than once in Ente, grouped;
  "Select extra copies" and Remove moves the extra copies to Ente's trash. The copy that
  stays is put into every album the others were in first
- Favorites (pull-down menu of the grid), and a heart on favorite photos in the grid
- Memories instead of "On this day": 1 month, 3 months and half a year ago (the nearest
  day with photos), this day in earlier years, and the best of this month in earlier years
  (favorites first, then picked across the month)
- A few photos of each day show big: some videos (3 x 2 when wide, 2 x 3 when upright) and
  some photos. The topmost big video in view plays by itself without sound (on Wi-Fi, for
  videos up to 60 MB); big tiles get sharper thumbnails
- The list of photos is read once more after this update: on this phone most photos had
  lost their content fingerprint, so photos already in Ente were not always recognised

* Thu Nov 19 2026 Enteup contributors - 0.10.0-1
- Space on the main page: how much of the Ente plan is used and free (bar and numbers, with
  the number of files and the plan's end date; family plans and bonus storage counted as in
  Ente's apps), a warning when it is almost full, and the free space on the phone. It is
  asked again after uploads and after deleting for good; tap it to ask at once

* Sat Nov 14 2026 Enteup contributors - 0.9.0-2
- Zooming a photo keeps the spot between the fingers in place (it zoomed towards the top
  left corner); a double tap zooms in on the spot tapped
- Thumbnails smaller again: 240 px on the short side, lower quality. Thumbnails already on
  the phone are shrunk once at the next start, without fetching them again

* Fri Nov 13 2026 Enteup contributors - 0.9.0-1
- Swipe between photos full screen (or use the arrows at the sides, also while a video
  plays); the header shows where you are (12/230)
- Favorites: a heart in the full-screen view, and "Favorite" for a selection; Ente's own
  favorites album is used (made when there is none). Removing a photo from favorites works
  as in Ente's apps: it stays in its other albums, or goes to "Uncategorized"
- More for a selection in the grid: add to an album (or a new one), move to another album
  (inside an album), save all to the phone, favorite; in albums shared with you: save
- Ente's trash (pull-down menu of the grid): see what is in it, restore photos into the
  album they left (or "Uncategorized"), delete them for good, empty it
- On this day (pull-down menu of the grid): photos taken on today's date in earlier years
- Cover: how many files wait to be backed up (or "all backed up"), the time of the last
  backup, whether automatic backup is on; a cover button uploads what is new
- A photo that left an album and came back between two updates (trash, then restore) was
  shown as gone; the last change now wins

* Thu Nov 12 2026 Enteup contributors - 0.8.0-2
- Ente's photo grid: a select button at the top left of the page starts (and ends)
  selecting, besides "Select" in the pull-down menu and pressing and holding a photo
- The save button in the full-screen view and the video mark in the grid had no picture
  (icons this phone does not have); they use existing ones now

* Thu Nov 12 2026 Enteup contributors - 0.8.0-1
- Photos already in Ente are not uploaded again, also ones uploaded from another phone or
  the web: before uploading, the app reads what is new in the account and compares each
  photo's content ("Already in Ente (album ...)"). Photos in Ente's trash do not count
- Automatic backup (main page): every 15 minutes, also while the app is closed, new photos
  from the switched-on sources go to Ente. Only on Wi-Fi unless switched off there; not
  while the battery is below 20% and not charging; never at the same time as the app's own
  uploads. The main page tells when it last ran and what it did
- Ente's photo grid: select several photos (press and hold one, or "Select" in the pull-down
  menu; tap a day to take all of it) and move them to Ente's trash together
- The list of photos is read once more after this update, for the content comparison

* Thu Nov 12 2026 Enteup contributors - 0.7.0-2
- 0.7.0-1 showed only a white page: the main window did not load (a signal named like the
  change signal Qt makes for a property). All pages now checked on the phone's own Qt

* Thu Nov 12 2026 Enteup contributors - 0.7.0-1
- Photos and videos from Ente, full screen: buttons to save, share and move to the trash
- Save: the original (also HEIC, also both parts of a live photo) goes to Pictures/Ente or
  Videos/Ente, under its name in Ente and dated when it was taken; nothing is overwritten.
  Saved copies are never uploaded back
- Share: saves the copy, then opens the system's share menu with it
- Move to Ente's trash (after a few seconds to cancel): the photo leaves all its albums; Ente
  keeps it in its trash for 30 days. Only for photos in your own albums

* Thu Nov 12 2026 Enteup contributors - 0.6.0-1
- Videos from Ente play in the app, with sound: tap to pause, tap the time bar to jump
- Live photos show their picture with a LIVE button that plays the movement
- Captions written in Ente show under the photo
- Albums others share with you: their own section in the album list ("from ..."); as in
  Ente's apps they stay out of the timeline. Needs one new sign-in after this update (the
  album list says so when there are such albums)
- Full photos and videos kept on the phone: at most 8 and at most 1 GB; a download that
  would fill the phone is refused with a message
- The list of photos is read once more after this update, to pick up the captions
- Sailjail permission Audio

* Thu Nov 12 2026 Enteup contributors - 0.5.0-1
- Browse the photos in Ente: "Browse photos in Ente" shows all photos as a grid by the day
  taken (hidden and archived ones left out, as in Ente's apps), the albums from its pull-down
  menu. Tap a photo to load it full screen; pinch or double-tap to zoom
- The first update reads the list of all photos (a few minutes for many); later updates read
  only the changes. Thumbnails load as they come into view and are kept smaller than Ente's
  (288 px, about 11 KB each; at most 200 MB); the last 8 full photos are kept
- Videos and pictures this phone cannot show (HEIC) show Ente's preview for now
- Signing out removes the list, the thumbnails and the full photos from the phone
- Albums hidden in Ente are no longer offered for uploads

* Thu Nov 12 2026 Enteup contributors - 0.4.0-1
- Empty and broken files are skipped instead of uploaded or failed: empty files, files of
  zeros, files that are no picture or video, pictures cut off or damaged, videos cut off or
  whose recording never finished. The reason shows in the list; they are left out of later
  uploads until they change, and each source tells how many it left out

* Thu Nov 12 2026 Enteup contributors - 0.3.0-1
- Upload sources: any number of folders (on the phone, the memory card or Android's
  storage) instead of the camera folders alone; suggestions for folders with photos, and a
  folder browser to add others. Each source can be switched off, and can upload into an
  album of its own instead of the main album (press and hold it)
- "Upload new from sources" sends everything new from all switched-on sources; a source can
  also be uploaded on its own from its menu. The camera folders are the first sources
- Sailjail permissions Downloads and Documents, for sources there

* Thu Nov 12 2026 Enteup contributors - 0.2.0-1
- Every photo in the upload list shows a small preview and when it was taken (the time Ente
  files it under); tap it to see it full screen, pinch or double-tap to zoom. Videos open in
  the phone's player
- New page "Uploaded photos" (pull-down menu): everything uploaded from this phone, newest
  first, grouped by the day taken. Photos uploaded with 0.1.0 get their time read once

* Thu Nov 12 2026 Enteup contributors - 0.1.0-3
- After an upload the page says where to find the photos in Ente: in the chosen album, and
  in the timeline at the date each was taken

* Thu Nov 12 2026 Enteup contributors - 0.1.0-2
- Sign in: the address is sent in lower case (the keyboard may capitalise it), and an
  address without an account says which address and server were asked

* Thu Nov 12 2026 Enteup contributors - 0.1.0-1
- Initial release: sign in with the mailed code (and authenticator code), choose or create
  an album, upload chosen photos and videos or everything new in the camera folder;
  files already uploaded from this phone are skipped
