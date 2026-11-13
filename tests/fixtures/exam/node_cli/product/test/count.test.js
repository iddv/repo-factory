'use strict'
const test = require('node:test')
const assert = require('node:assert')
const { count } = require('../lib/count.js')

test('three words', () => { assert.strictEqual(count('a b c'), 3) })
test('nothing', () => { assert.strictEqual(count('  '), 0) })
