#!/usr/bin/env python
# coding=utf-8

import random
import math

from collections import defaultdict, Counter
import csv
import argparse

ITERATIONS = 10000

model = None
win_loss_histogram = None
games_played = {}

def read_tournament(lines):
  first_day, last_day = lines[0].split(' ')
  del lines[0]
  rounds = []
  group = None
  for line in lines:
    if not line:
      continue
    line = line.rstrip()
    tag = line.lstrip()
    depth = len(line) - len(tag)
    assert depth in (0, 2, 4)
    if depth == 0:
      rounds.append((tag, []))
    elif depth == 2:
      rounds[-1][1].append((tag, []))
    else:
      rounds[-1][1][-1][1].append(tag)
  return first_day, last_day, rounds


def simulate_game(team1, team2):
  already_played = games_played.get((team1, team2)) or tuple(reversed(games_played.get((team2, team1), [])))
  if already_played:
    return already_played
  score1 = model[team1]
  score2 = model[team2]
  difference = score1 - score2
  bucket = abs(difference)
  if bucket < 0.1:
    bucket = 0
  else:
    bucket = math.log(bucket) / math.log(2) - math.log(0.1) / math.log(2)
  histogram_1 = win_loss_histogram[int(bucket)]
  histogram_2 = win_loss_histogram[int(bucket) + 1]
  fraction = bucket - int(bucket)
  histogram = [int(histogram_1[i] * (1 - fraction) + histogram_2[i] * fraction) for i in range(3)]
  total = sum(histogram)
  r = random.randint(0, total - 1)
  res = 0
  if r <= histogram[0]:
    res = -1
  elif r > histogram[1] + histogram[0]:
    res = 1
  if difference < 0:
    res = -res
  return 1 + res, 1 - res


def simulate_knock_out(team1, team2):
  g1, g2 = simulate_game(team1, team2)
  if g1 > g2:
    return team1
  elif g2 > g1:
    return team2
  return random.choice((team1, team2))

def simulate_group(group):
  games = [(team1, team2) for idx, team2 in enumerate(group) for team1 in group[idx + 1:]]
  points = defaultdict(int)
  goals_pro = defaultdict(int)
  goals_against = defaultdict(int)
  for game in games:
    team1 = game[0]
    team2 = game[1]
    goals1, goals2 = simulate_game(team1, team2)
    if goals1 == goals2:
      points[team1] += 1
      points[team2] += 1
    elif goals1 > goals2:
      points[team1] += 3
    else:
      points[team2] += 3
    goals_pro[team1] += goals1
    goals_against[team1] += goals2
    goals_pro[team2] += goals2
    goals_against[team2] += goals1
  teams = sorted(goals_against.keys(),
                 key=lambda team: points[team] + float(goals_pro[team] - goals_against[team]) / 100 + float(goals_pro[team] / 10000), reverse=True)
  return teams



def group_chances(group):
  results = defaultdict(lambda: [0, 0])
  for i in range(100000):
    team1, team2 = simulate_group(group)
    results[team1][0] += 1
    results[team2][1] += 1

  for k, v in results.items():
    print '%s: %2.2f%% %2.2f%%' % (k, v[0] / 1000., v[1] / 1000.)


def simulate_tournament(tournament):
  results = {}
  for round_name, round in tournament:
    third_places = []
    for group_name, group in round:
      group = [results.get(team, team) for team in group]
      if len(group) == 2:
        results[group_name] = simulate_knock_out(group[0], group[1])
      else:
        for idx, team in enumerate(simulate_group(group)):
          key = group_name + str(idx + 1)
          results[key] = team
          if key.endswith('3'):
            third_places.append(team)
      if third_places:
        random.shuffle(third_places)
        for idx, team in enumerate(third_places):
          results['Q' + str(idx + 1)] = team

  return results['Winner']


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description='Simulate a tournament.')
  parser.add_argument('tournament', type=str, default='france_2016.tournament',
                      help='the tournament to simulate')
  parser.add_argument('match', type=str, nargs='?', default='',
                      help='Match to focus on. Team1-Team2=Goals1-Goals2. Pretend that that is the outcome.')

  args = parser.parse_args()

  model = file('model.txt').read().splitlines()
  win_loss_histogram = eval(model[0])
  del model[0]
  model = [l.split(':') for l in model]
  model = {team: float(score) for team, score in model}

  first_day, last_day, tournament = read_tournament(file(args.tournament).read().splitlines())
  games_played = {(r['team1'], r['team2']): (int(r['goals1']), int(r['goals2']))
                  for r in csv.DictReader(file('results-friendly.csv')) if first_day <= r['date'] <= last_day}

  if args.match:
    if '=' in args.match:
      teams, result = args.match.split('=')
    else:
      teams = args.match
      result = None
    team1, team2 = teams.split('-')
    match_res = Counter()
    for i in range(ITERATIONS):
      match_res[str(simulate_game(team1, team2))] += 1
    for outcome, c in match_res.most_common():
      print outcome, '%2.2f%%' % ((100.0 * c) / ITERATIONS)
    if result:
      goals1, goals2 = map(int, result.split('-'))
      games_played[(team1, team2)] = (goals1, goals2)

  res = Counter()
  for i in range(ITERATIONS):
    res[simulate_tournament(tournament)] += 1

  for team, c in res.most_common():
    print team, '%2.2f%%' % ((100.0 * c) / ITERATIONS)
