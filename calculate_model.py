#!/usr/bin/env python

import csv
import math
import datetime

from collections import defaultdict, Counter

ITERATIONS = 200
ADJUST_FACTOR = 0.05

def reject_youth(game):
  postfix = game['team1'].split(' ')[-1]
  if len(postfix) == 3 and postfix[0] == 'U':
    return False
  return True

def read_csv(path, base_weight):
  reader = csv.DictReader(file(path))
  games = list(reader)
  sum_diff = 0.0
  sum_count = 0
  year = 2016
  prev_month = None
  now = datetime.datetime.now()
  for game in games:
    goals1, goals2 = map(int, game['result'].split(':'))
    game['goals1'] = goals1
    game['goals2'] = goals2
    day, month, _ = game['date'].split('.')
    day = int(day)
    month = int(month)
    if prev_month and prev_month < month:
      year -= 1
    prev_month = month
    game['weight'] = base_weight / max((now - datetime.datetime(year, month, day)).days / 180.0, 1.0)
    game['date'] = '%d-%02d-%02d' % (year, month, day)
    sum_count += 1
    if 'Netherlands' in (game['team1'], game['team2']):
      print '%(team1)s - %(team2)s  %(goals1)d - %(goals2)d   (%(weight)2.2f)' % game
  return games, sum_diff, sum_count


def main():
  games = []
  sum_diff = 0
  sum_count = 0
  for path, weight in [('results-friendly.csv', 0.5), ('results-qualifiers.csv', 1.0), ('results-worldcup18.csv', 0.8)]:
    g, sd, sc = read_csv(path, weight)
    sum_diff += sd
    sum_count += sc
    games.extend(g)

  scores = defaultdict(float)
  team_count = Counter()

  games = filter(reject_youth, games)
  for game in games:
    team_count[game['team1']] += 1
    team_count[game['team2']] += 1

  few_results = set(team for team, nr in team_count.most_common() if nr <= 2)
  games = filter(lambda game:not game['team1'] in few_results and not game['team2'] in few_results, games)

  home_advantage = float(sum_diff) / sum_count
  print 'home advantage:', home_advantage

  win_loss_histogram = None

  for iteration in range(ITERATIONS):
    win_loss_histogram = defaultdict(lambda :[0, 0, 0])
    square_sum = 0
    sum_count = 0
    for game in games:
      prediction = scores[game['team1']] - scores[game['team2']] + 0.2
      result = game['goals1'] - game['goals2']
      if result > 3:
        result = 2 + (result - 2) / 2

      if iteration == ITERATIONS - 1 and game['date'] > '2014':
        if result == 0:
          score_bucket = 1
        elif result < 0:
          score_bucket = 0
        else:
          score_bucket = 2
        if prediction < 0:
          score_bucket = 2 - score_bucket
        bucket = abs(prediction)
        if bucket < 0.1:
          bucket = 0
        else:
          bucket = int(math.log(bucket) / math.log(2) - math.log(0.1) / math.log(2))
        win_loss_histogram[bucket][score_bucket] += 1

      difference = result - prediction
      scores[game['team1']] += difference * ADJUST_FACTOR * game['weight']
      scores[game['team2']] -= difference * ADJUST_FACTOR * game['weight']

      if game['date'] > '2016-04-01':
        square_sum += abs(difference)
        sum_count += 1

  model = file('model.txt', 'w')

  model.write(repr(dict(win_loss_histogram)) + '\n')
  i = 10
  for name, score in sorted(scores.iteritems(), key=lambda t:t[1], reverse=True):
    if i > 0 or name == 'Germany':
      print name, score
    i -= 1
    model.write('%s:%2.3f\n' % (name, score))



if __name__ == '__main__':
  main()