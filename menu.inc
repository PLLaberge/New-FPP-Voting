<?php

# The plugin is a separate FastAPI service (see scripts/fpp_install.sh), not a
# PHP page FPP renders itself — so every entry below is an http:// URL, which
# FPP's menu renderer opens as a plain external link (see the preg_match
# below) instead of routing through plugin.php. That is deliberate: it means
# this file never has to duplicate a page FPP would otherwise not know how to
# reach, and it works whether the service is running as the systemd unit
# fpp_install.sh sets up or reached through the tunnel from off the Pi.
#
# PLUGIN_GUIDELINES.md #9.1: at most ONE entry per type (status/content/output/
# help).

$host = $_SERVER['HTTP_HOST'] ?? 'localhost';
$hostOnly = preg_replace('/:\d+$/', '', $host);   # FPP's own port, not ours
$port = getenv('FPPVOTE_PORT') ?: '8000';
$base = "http://$hostOnly:$port";

$menuEntries = Array(
	Array(
		'text' => 'Voting - Open Voting Page',
		'type' => 'status',
		'page' => "$base/",
		'wrap' => 0
	),
	Array(
		'text' => 'Voting - Admin',
		'type' => 'content',
		'page' => "$base/admin",
		'wrap' => 0
	),
	Array(
		'text' => 'Voting - Source & Docs',
		'type' => 'help',
		'page' => 'https://github.com/PLLaberge/New-FPP-Voting',
		'wrap' => 0
	)
);

##############################################################################
# Display the menu entries for this plugin.
#
# It is expected that two variables are already set:
# $plugin - contains the name of the current plugin directory/repoName
# $menu - contains the name of the menu section/type
foreach ($menuEntries as $entry)
{
	if ($entry['type'] != $menu)
		continue;

	if (preg_match('/^http.?:\/\//', $entry['page']))
	{
		printf("<li><a href='%s' target='_blank'>%s</a></li>\n",
			$entry['page'], $entry['text']);
	}
	else
	{
		$nopage = '';
		if (isset($entry['wrap']) && ($entry['wrap'] == 0))
			$nopage = '&nopage=1';

		printf("<li><a href='plugin.php?plugin=%s&page=%s%s'>%s</a></li>\n",
			$plugin, $entry['page'], $nopage, $entry['text']);
	}
}
?>
