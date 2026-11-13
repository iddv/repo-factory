'use strict'

function count (text) {
  return text.split(/\s+/).filter(Boolean).length
}

module.exports = { count }
