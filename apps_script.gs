var SOFASCORE_SERVICE = 'https://web-production-05c176.up.railway.app';
var PLAYER_ID = 826643;

function fetchSofascore(path) {
  var response = UrlFetchApp.fetch(SOFASCORE_SERVICE + path, {
    method: 'get',
    muteHttpExceptions: true,
    headers: { 'Accept': 'application/json' },
  });

  var code = response.getResponseCode();
  var body = response.getContentText();

  if (code !== 200) {
    throw new Error('Sofascore service returned ' + code + ': ' + body);
  }

  return JSON.parse(body);
}

function fetchMbappeLastMatchStats() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('matches');
  var lastRow = sheet.getLastRow();
  var birthDate = new Date(1998, 11, 20); // 20 December 1998

  var clubMatch = fetchSofascore('/player/' + PLAYER_ID + '/last-match/full?context=club');
  var nationalMatch = fetchSofascore('/player/' + PLAYER_ID + '/last-match/full?context=national');

  Logger.log(
    'Club: ' + clubMatch.rating + ' - ' + clubMatch.tournament + ' (' + clubMatch.date + ')'
  );
  Logger.log(
    'National: ' + nationalMatch.rating + ' - ' + nationalMatch.tournament + ' (' + nationalMatch.date + ')'
  );

  processLastMatchIfNew_(sheet, lastRow, clubMatch, birthDate);
  processLastMatchIfNew_(sheet, lastRow, nationalMatch, birthDate);
}

function processLastMatchIfNew_(sheet, lastRow, payload, birthDate) {
  if (!payload || !payload.timestamp) {
    return;
  }

  var matchDate = new Date(payload.timestamp * 1000);
  var formattedDate = Utilities.formatDate(matchDate, Session.getScriptTimeZone(), 'dd/MM/yyyy');

  if (lastRow > 1 && sheet.getRange(lastRow, 4).getValue() === formattedDate) {
    Logger.log('Match already recorded for ' + formattedDate);
    return;
  }

  var event = payload.event;
  if (!event) {
    Logger.log('No full event details yet for ' + formattedDate + ' — summary only');
    return;
  }

  var playerTeamId = getMbappeTeamId_(event);
  var opponent = getOpponentName_(event, playerTeamId);
  var score = formatScore_(event.homeScore, event.awayScore);
  var goals = countPlayerGoals_(payload.incidents, PLAYER_ID);
  var assists = countPlayerAssists_(payload.incidents, PLAYER_ID);
  var minutesPlayed = payload.playerStats && payload.playerStats.minutesPlayed
    ? String(payload.playerStats.minutesPlayed)
    : '';
  var matchNumber = String(lastRow);

  // Keep your existing sheet / Firestore pipeline below.
  Logger.log({
    matchNumber: matchNumber,
    team: mapTeamName_(playerTeamId),
    opponent: opponent,
    date: formattedDate,
    competition: payload.tournament,
    goals: goals,
    assists: assists,
    minutesPlayed: minutesPlayed,
    score: score,
    rating: payload.rating,
  });
}

function getMbappeTeamId_(event) {
  var teamIds = [2829, 4481, 1644, 1653];
  var homeId = event.homeTeam && event.homeTeam.id;
  var awayId = event.awayTeam && event.awayTeam.id;

  if (teamIds.indexOf(homeId) !== -1) {
    return homeId;
  }
  if (teamIds.indexOf(awayId) !== -1) {
    return awayId;
  }
  return null;
}

function mapTeamName_(teamId) {
  if (teamId === 2829) return 'Real Madrid';
  if (teamId === 4481) return 'France';
  if (teamId === 1644) return 'PSG';
  if (teamId === 1653) return 'Monaco';
  return '';
}

function getOpponentName_(event, playerTeamId) {
  if (!event || !playerTeamId) return '';
  if (event.homeTeam && event.homeTeam.id === playerTeamId) {
    return event.awayTeam ? event.awayTeam.name : '';
  }
  return event.homeTeam ? event.homeTeam.name : '';
}

function formatScore_(homeScore, awayScore) {
  var home = homeScore && (homeScore.current != null ? homeScore.current : homeScore.display);
  var away = awayScore && (awayScore.current != null ? awayScore.current : awayScore.display);
  if (home == null || away == null) return '';
  return home + '-' + away;
}

function countPlayerGoals_(incidents, playerId) {
  if (!incidents) return '0';
  var count = incidents.filter(function(incident) {
    return incident.incidentType === 'goal' &&
      incident.player &&
      incident.player.id === playerId;
  }).length;
  return String(count);
}

function countPlayerAssists_(incidents, playerId) {
  if (!incidents) return '0';
  var count = incidents.filter(function(incident) {
    return incident.incidentType === 'goal' &&
      incident.assist1 &&
      incident.assist1.id === playerId;
  }).length;
  return String(count);
}
