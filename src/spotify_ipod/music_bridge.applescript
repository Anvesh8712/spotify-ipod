on run argv
	if (count of argv) is 0 then error "Missing Music bridge command."
	set cmd to item 1 of argv
	if cmd is "ping" then
		return my pingMusic()
	else if cmd is "import" then
		if (count of argv) < 2 then error "import requires a file path."
		return my importFile(item 2 of argv)
	else if cmd is "ensure-playlist" then
		if (count of argv) < 3 then error "ensure-playlist requires a name and marker."
		return my ensurePlaylist(item 2 of argv, item 3 of argv)
	else if cmd is "rename-playlist" then
		if (count of argv) < 3 then error "rename-playlist requires a persistent ID and name."
		my renamePlaylist(item 2 of argv, item 3 of argv)
		return "ok"
	else if cmd is "rebuild-playlist" then
		if (count of argv) < 2 then error "rebuild-playlist requires a playlist persistent ID."
		set trackIds to {}
		if (count of argv) ≥ 3 then
			set AppleScript's text item delimiters to ","
			set trackIds to text items of item 3 of argv
			set AppleScript's text item delimiters to ""
		end if
		my rebuildPlaylist(item 2 of argv, trackIds)
		return "ok"
	else if cmd is "find-comment" then
		if (count of argv) < 2 then error "find-comment requires a comment."
		return my findByComment(item 2 of argv)
	else if cmd is "find-comments" then
		if (count of argv) < 2 then error "find-comments requires a comment."
		return my findAllByComment(item 2 of argv)
	else if cmd is "delete-track" then
		if (count of argv) < 2 then error "delete-track requires a persistent ID."
		my deleteLibraryTrack(item 2 of argv)
		return "ok"
	else if cmd is "track-exists" then
		if (count of argv) < 2 then error "track-exists requires a persistent ID."
		return my trackExists(item 2 of argv)
	else if cmd is "track-location" then
		if (count of argv) < 2 then error "track-location requires a persistent ID."
		return my trackLocation(item 2 of argv)
	else if cmd is "set-comment" then
		if (count of argv) < 3 then error "set-comment requires a persistent ID and comment."
		my setTrackComment(item 2 of argv, item 3 of argv)
		return "ok"
	else if cmd is "convert-aac" then
		if (count of argv) < 2 then error "convert-aac requires a persistent ID."
		return my convertToAac(item 2 of argv)
	else
		error "Unknown Music bridge command: " & cmd
	end if
end run

on pingMusic()
	with timeout of 30 seconds
		tell application "Music"
			if not running then launch
			return name of it
		end tell
	end timeout
end pingMusic

on importFile(posixPath)
	set prevApp to my frontmostProcessName()
	with timeout of 90 seconds
		tell application "Music"
			if not running then launch
			set added to add (POSIX file posixPath)
			try
				set trackPID to persistent ID of item 1 of added
			on error
				try
					set trackPID to persistent ID of added
				on error
					error "Music imported the file but did not return a track ID."
				end try
			end try
		end tell
	end timeout
	my hideMusic()
	my restoreFrontmost(prevApp)
	return trackPID
end importFile

on ensurePlaylist(playlistName, marker)
	with timeout of 60 seconds
		tell application "Music"
			if not running then launch
			repeat with p in (every user playlist whose special kind is none)
				try
					if description of p is marker then
						if name of p is not playlistName then set name of p to playlistName
						return persistent ID of p
					end if
				end try
			end repeat
			set p to make new user playlist with properties {name:playlistName, description:marker}
			return persistent ID of p
		end tell
	end timeout
end ensurePlaylist

on renamePlaylist(playlistPID, playlistName)
	with timeout of 30 seconds
		tell application "Music"
			set p to first user playlist whose persistent ID is playlistPID
			set name of p to playlistName
		end tell
	end timeout
end renamePlaylist

on rebuildPlaylist(playlistPID, trackPIDs)
	set prevApp to my frontmostProcessName()
	with timeout of 3600 seconds
		tell application "Music"
			if not running then launch
			set p to first user playlist whose persistent ID is playlistPID
			try
				delete every track of p
			end try
			repeat with tid in trackPIDs
				set token to tid as text
				if token is not "" then
					try
						set t to first track of library playlist 1 whose persistent ID is token
						duplicate t to p
					end try
				end if
			end repeat
		end tell
	end timeout
	my hideMusic()
	my restoreFrontmost(prevApp)
end rebuildPlaylist

on findByComment(marker)
	with timeout of 20 seconds
		tell application "Music"
			try
				return persistent ID of (first track of library playlist 1 whose comment is marker)
			on error
				return ""
			end try
		end tell
	end timeout
end findByComment

on findAllByComment(marker)
	with timeout of 60 seconds
		tell application "Music"
			try
				set ids to persistent ID of (every track of library playlist 1 whose comment is marker)
				set AppleScript's text item delimiters to ","
				set out to ids as text
				set AppleScript's text item delimiters to ""
				return out
			on error
				return ""
			end try
		end tell
	end timeout
end findAllByComment

on deleteLibraryTrack(trackPID)
	with timeout of 60 seconds
		tell application "Music"
			set t to first track of library playlist 1 whose persistent ID is trackPID
			delete t
		end tell
	end timeout
end deleteLibraryTrack

on trackExists(trackPID)
	with timeout of 30 seconds
		tell application "Music"
			try
				set t to first track of library playlist 1 whose persistent ID is trackPID
				return "yes"
			on error
				return "no"
			end try
		end tell
	end timeout
end trackExists

on trackLocation(trackPID)
	with timeout of 30 seconds
		tell application "Music"
			try
				set t to first track of library playlist 1 whose persistent ID is trackPID
				set loc to location of t
				return POSIX path of loc
			on error
				return ""
			end try
		end tell
	end timeout
end trackLocation

on setTrackComment(trackPID, marker)
	with timeout of 30 seconds
		tell application "Music"
			set t to first track of library playlist 1 whose persistent ID is trackPID
			set comment of t to marker
		end tell
	end timeout
end setTrackComment

on convertToAac(trackPID)
	-- Use Music's built-in AAC Encoder (same path as File → Convert).
	-- Avoid activate; restore the previous frontmost app afterward.
	set prevApp to my frontmostProcessName()
	with timeout of 600 seconds
		tell application "Music"
			if not running then launch
			set current encoder to encoder "AAC Encoder"
			set t to first track of library playlist 1 whose persistent ID is trackPID
			set converted to convert t
			set newPID to ""
			try
				set newPID to persistent ID of item 1 of converted
			end try
			if newPID is "" then
				try
					set newPID to persistent ID of converted
				end try
			end if
			if newPID is "" then error "Music convert did not return a track ID."
			if newPID is not trackPID then
				try
					delete t
				end try
			end if
		end tell
	end timeout
	my hideMusic()
	my restoreFrontmost(prevApp)
	return newPID
end convertToAac

on frontmostProcessName()
	try
		tell application "System Events"
			return name of first application process whose frontmost is true
		end tell
	on error
		return ""
	end try
end frontmostProcessName

on restoreFrontmost(processName)
	if processName is "" then return
	if processName is "Music" then return
	try
		tell application "System Events"
			set frontmost of process processName to true
		end tell
	end try
end restoreFrontmost

on hideMusic()
	try
		tell application "System Events"
			set visible of process "Music" to false
		end tell
	end try
end hideMusic
