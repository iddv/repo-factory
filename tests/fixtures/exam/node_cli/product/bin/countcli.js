#!/usr/bin/env node
'use strict'
const { count } = require('../lib/count.js')

let text = ''
process.stdin.on('data', d => { text += d })
process.stdin.on('end', () => { process.stdout.write(`${count(text)}\n`) })
