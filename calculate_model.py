#!/usr/bin/env python

import csv
import math

from collections import defaultdict

ITERATIONS = 200
ADJUST_FACTOR = 0.05

if __name__ == '__main__':
  reader = csv.DictReader(file('results.csv'))
  games = list(reader)

  scores = defaultdict(float)
  names = dict()

  sum_diff = 0.0
  sum_count = 0
  for game in games:
    game['goals1'] = int(game['goals1'])
    game['goals2'] = int(game['goals2'])
    sum_diff += game['goals1'] - game['goals2']
    sum_count += 1
    names[game['code1']] = game['team1']
    names[game['code2']] = game['team2']

  home_advantage = float(sum_diff) / sum_count
  print 'home advantage:', home_advantage

  win_loss_histogram = None

  for iteration in range(ITERATIONS):
    win_loss_histogram = defaultdict(lambda :[0, 0, 0])
    square_sum = 0
    sum_count = 0
    for game in games:
      prediction = scores[game['code1']] - scores[game['code2']] + 0.2
      result = game['goals1'] - game['goals2']

      if iteration == ITERATIONS - 1 and game['date'] > '2010':
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
      scores[game['code1']] += difference * ADJUST_FACTOR
      scores[game['code2']] -= difference * ADJUST_FACTOR

      if game['date'] > '2014-05-01':
        square_sum += abs(difference)
        sum_count += 1

  model = file('model.txt', 'w')

  model.write(repr(dict(win_loss_histogram)) + '\n')
  for code, score in sorted(scores.iteritems(), key=lambda t:t[1], reverse=True):
    if score > 2.0:
      print names[code], score
    model.write('%s:%2.3f\n' % (names[code], score))



