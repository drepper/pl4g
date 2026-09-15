; Highlighting for PL4G.
;
; The capture names are the ones tree-sitter's own highlighter knows, so that an
; editor with no configuration for this language still colours it sensibly.

; -- comments -----------------------------------------------------------------
;
; A documentation comment belongs to what follows it and is worth telling apart
; from one that is only a remark.
(doc_comment) @comment.documentation
(line_comment) @comment

; -- names --------------------------------------------------------------------

(function_definition name: (identifier) @function)
(parameter name: (identifier) @variable.parameter)
(variable_definition name: (identifier) @variable)
(variable_statement name: (identifier) @variable)
(assignment target: (identifier) @variable)
(type (identifier) @type)
(attribute name: (identifier) @attribute)
(attribute_argument name: (identifier) @property)

; A name standing on its own is a mention of something defined elsewhere.
(identifier) @variable

; -- literals -----------------------------------------------------------------

(integer_literal) @number
(float_literal) @number.float
(string_literal) @string
(boolean_literal) @boolean

; -- words --------------------------------------------------------------------

["fn" "let" "return"] @keyword
(mutable) @keyword.modifier

; -- symbols ------------------------------------------------------------------

["&" "|" "^" "~"] @operator
["=" "≠" "<" ">" "≤" "≥" "<=" ">="] @operator
["∧" "∨" "⊕" "⊼" "⊽" "¬"] @operator
["+" "-" "×" "÷" "%" "⊞" "⊟" "⊠"] @operator
["«" "»" "↺" "↻"] @operator
["#" "⍴" "⧺" "++" "⌈" "⌊"] @operator
["↓" "↑" "↕" "⇕" "ⁿ"] @operator
["⌜" "⌝"] @punctuation.bracket
["∣" "∤"] @operator
(exponent_literal) @number
["and" "or"] @keyword.operator
["←"] @operator
["→" "->"] @punctuation.delimiter
["(" ")" "{" "}" "@[" "]"] @punctuation.bracket
["," ":" ";" "="] @punctuation.delimiter

; -- modules ------------------------------------------------------------------

(module_import name: (identifier) @module)
(member_expression base: (identifier) @module)
(member_expression name: (identifier) @variable)
["import"] @keyword.import
