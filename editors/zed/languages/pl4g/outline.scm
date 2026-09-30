; What the outline panel shows, which is every definition a file makes.
;
; Zed's own captures and not tree-sitter's: `@item` is the row, `@name` is what
; it is called, and `@context` is what is shown around the name -- the word that
; says which kind of definition this is, so that a list of them reads like the
; program.
;
; A record's fields and an enumeration's values are rows of their own, and Zed
; nests them under the definition they are inside of.

(function_definition
  "fn" @context
  name: (identifier) @name) @item

(type_definition
  "type" @context
  name: (identifier) @name) @item

(type_part
  name: (identifier) @name) @item

(enum_definition
  "enum" @context
  name: (identifier) @name) @item

(enum_value
  name: (identifier) @name) @item

(unit_definition
  "unit" @context
  name: (identifier) @name) @item

(bundle_definition
  "bundle" @context
  name: (identifier) @name) @item

(module_import
  "let" @context
  name: (identifier) @name) @item

; A variable at the top level is a definition; one inside a body is a statement
; and is not in the outline, which is what the two rules being separate is for.
(variable_definition
  "let" @context
  name: (identifier) @name) @item
