// The external scanner: the tokens a context-free grammar cannot produce.
//
// All three come from the layout rules.  A newline ends a statement, but only
// where one is wanted: a blank line and a line holding nothing but a comment
// are not the end of a statement, so no token is produced for them.  An indent
// and a dedent stand for a column that grew or shrank, and are what a block
// written by indentation is delimited by.
//
// A line break inside brackets is not one of these and is not decided here.
// The scanner is asked only where the parser would accept one of the three, and
// inside brackets it would accept none of them, so it is not asked at all; the
// grammar reads such a break as an extra instead.  What that costs is stated
// where the extras are declared.
//
// A comment is not one of them: both kinds are ordinary tokens the grammar
// treats as extras.  The scanner has to know about them all the same, because a
// line holding nothing but a comment is not the end of a statement and does not
// say anything about the column a block is measured against.
//
// The indent stack is serialized between parses, which is what lets the parser
// resume in the middle of a file after an edit.

#include "tree_sitter/parser.h"

#include <stdlib.h>

enum TokenType {
  NEWLINE,
  INDENT,
  DEDENT,
};

// Deep enough for any program a person or a generator writes; the serialized
// state has to fit in TREE_SITTER_SERIALIZATION_BUFFER_SIZE bytes.
#define MAX_DEPTH 64

typedef struct {
  uint8_t depth;
  uint16_t columns[MAX_DEPTH];
} Scanner;

void *tree_sitter_pl4g_external_scanner_create(void) {
  Scanner *scanner = malloc(sizeof(Scanner));
  scanner->depth = 1;
  scanner->columns[0] = 0;
  return scanner;
}

void tree_sitter_pl4g_external_scanner_destroy(void *payload) {
  free(payload);
}

unsigned tree_sitter_pl4g_external_scanner_serialize(void *payload,
                                                     char *buffer) {
  Scanner *scanner = (Scanner *)payload;
  unsigned size = 0;
  buffer[size++] = (char)scanner->depth;
  for (uint8_t i = 0; i < scanner->depth; i++) {
    buffer[size++] = (char)(scanner->columns[i] & 0xFF);
    buffer[size++] = (char)(scanner->columns[i] >> 8);
  }
  return size;
}

void tree_sitter_pl4g_external_scanner_deserialize(void *payload,
                                                   const char *buffer,
                                                   unsigned length) {
  Scanner *scanner = (Scanner *)payload;
  scanner->depth = 1;
  scanner->columns[0] = 0;
  if (length == 0)
    return;
  unsigned size = 0;
  uint8_t depth = (uint8_t)buffer[size++];
  if (depth > MAX_DEPTH)
    depth = MAX_DEPTH;
  scanner->depth = depth;
  for (uint8_t i = 0; i < depth && size + 1 < length; i++) {
    uint16_t low = (uint8_t)buffer[size++];
    uint16_t high = (uint8_t)buffer[size++];
    scanner->columns[i] = (uint16_t)(low | (high << 8));
  }
}

static void skip(TSLexer *lexer) { lexer->advance(lexer, true); }

// The reference mark that introduces a comment, in UTF-8.
static bool at_comment(TSLexer *lexer) { return lexer->lookahead == 0x203B; }

bool tree_sitter_pl4g_external_scanner_scan(void *payload, TSLexer *lexer,
                                            const bool *valid_symbols) {
  Scanner *scanner = (Scanner *)payload;

  // Every one of these tokens is a marker with no text of its own, so the
  // whitespace before it is skipped rather than consumed: skipping leaves the
  // token empty, and an empty token is what lets the newline that ends a
  // statement and the indent that opens a block both stand at one place.
  bool saw_newline = false;
  for (;;) {
    if (lexer->lookahead == '\n') {
      saw_newline = true;
      skip(lexer);
    } else if (lexer->lookahead == ' ' || lexer->lookahead == '\t' ||
               lexer->lookahead == '\r') {
      skip(lexer);
    } else {
      break;
    }
  }

  if (lexer->eof(lexer)) {
    // Every block a file opened is closed at its end, so that a file ending
    // inside one still parses as far as it went.
    if (valid_symbols[DEDENT] && scanner->depth > 1) {
      scanner->depth--;
      lexer->result_symbol = DEDENT;
      return true;
    }
    if (valid_symbols[NEWLINE] && saw_newline) {
      lexer->result_symbol = NEWLINE;
      return true;
    }
    return false;
  }

  // A line with nothing but a comment on it says nothing about the layout: the
  // column a block is measured against is decided by the lines that have
  // something on them.  The newline before it still ends a statement.
  if (at_comment(lexer)) {
    if (valid_symbols[NEWLINE] && saw_newline) {
      lexer->result_symbol = NEWLINE;
      return true;
    }
    return false;
  }

  // The lexer knows the column, which is what makes this work after the
  // newline has already been given out: the indent that follows it measures
  // the same place over again rather than what is left of the line.
  uint32_t column = lexer->get_column(lexer);
  uint16_t current = scanner->columns[scanner->depth - 1];

  if (valid_symbols[INDENT] && column > current && scanner->depth < MAX_DEPTH) {
    scanner->columns[scanner->depth++] = (uint16_t)column;
    lexer->result_symbol = INDENT;
    return true;
  }

  if (valid_symbols[DEDENT] && column < current) {
    scanner->depth--;
    lexer->result_symbol = DEDENT;
    return true;
  }

  if (valid_symbols[NEWLINE] && saw_newline) {
    lexer->result_symbol = NEWLINE;
    return true;
  }

  return false;
}
