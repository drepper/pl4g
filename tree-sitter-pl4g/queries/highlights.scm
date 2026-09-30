; Highlighting for PL4G.
;
; The capture names are the ones tree-sitter's own highlighter knows, so that an
; editor with no configuration for this language still colours it sensibly.
;
; **Where two patterns match the same thing, the one written later wins.**  That
; is what tree-sitter's own highlighter does, what Neovim does, and what the
; compiler's colouriser was taught to do so that all three read this file the
; same way.  So the coarse answer is written first and the finer one after it:
; `(identifier) @variable` stands above everything that says which kind of name
; this one is, and a bare `=` is a delimiter until a comparison claims it.

; -- comments -----------------------------------------------------------------
;
; A documentation comment belongs to what follows it and is worth telling apart
; from one that is only a remark.  The two never overlap -- the scanner gives out
; one token or the other -- so which is written first says nothing.
(line_comment) @comment
(doc_comment) @comment.documentation

; -- names --------------------------------------------------------------------
;
; Every name, and then what each kind of name is.
(identifier) @variable

(function_definition name: (identifier) @function)
(call_expression function: (identifier) @function.call)
(parameter name: (identifier) @variable.parameter)
(lambda_parameter name: (identifier) @variable.parameter)
(named_argument name: (identifier) @variable.parameter)
(variable_definition name: (identifier) @variable)
(variable_statement name: (identifier) @variable)
(foreach_statement name: (identifier) @variable)
(pattern name: (identifier) @variable)
(capture name: (identifier) @variable)
(assignment target: (identifier) @variable)
(type (identifier) @type)
(type_definition name: (identifier) @type)
(enum_definition name: (identifier) @type)
(enum_value name: (identifier) @constant)
(type_part name: (identifier) @variable.member)
(member_expression name: (identifier) @variable.member)
(attribute name: (identifier) @attribute)
(attribute_argument name: (identifier) @property)
(label name: (identifier) @label)
(lifetime name: (identifier) @label)
(unit_definition name: (identifier) @type)
(unit_factor name: (identifier) @type)

; A name the compiler provides rather than the program: `⎕import` is a word of
; the grammar, and the rest are names beginning with the same glyph.
((identifier) @function.builtin
  (#match? @function.builtin "^⎕"))

; -- literals -----------------------------------------------------------------

(integer_literal) @number
(exponent_literal) @number
(float_literal) @number.float
(character_literal) @character
(string_literal) @string
(boolean_literal) @boolean

; -- words --------------------------------------------------------------------

["fn" "λ"] @keyword.function
; An operator standing where a name goes, and one the language gives no meaning.
; The first is a name and reads as one; the second is an operator and reads as
; one, which is what tells a reader which of the two a line is doing.
(operator_name) @function
(fresh_operator) @operator
; A pair a program defined reads as the brackets it is: what the language's own
; brackets are coloured, since what it does is what they do.
[(fresh_open) (fresh_close)] @punctuation.bracket
"let" @keyword
"return" @keyword.return
["if" "elif" "else" "match"] @keyword.conditional
["while" "unless" "foreach" "break" "continue"] @keyword.repeat
["type" "enum" "unit" "bundle"] @keyword.type
; A macro is a definition, and a hole is what stands where an argument will.
"macro" @keyword
(hole) @variable.parameter
; What a signature requires of its types and demands of its values.
["pre" "post"] @keyword
"comptime" @keyword.modifier
; `static` is the whole of what one node is, and `mut` of another.
(lasting) @keyword.modifier
(mutable) @keyword.modifier
["and" "or" "in"] @keyword.operator
["⎕import"] @keyword.import

; -- symbols ------------------------------------------------------------------
;
; The delimiters first, because two of them are also operators: `=` binds a name
; and compares two values, and `.` reaches into a record; which one it is comes
; from the shape it is in, which is what the patterns below say.
["," ":" ";" "=" "."] @punctuation.delimiter
["→" "->"] @punctuation.delimiter
["(" ")" "{" "}" "[" "]" "@["] @punctuation.bracket
["⟦" "⟧" "〈" "〉" "⸨" "⸩"] @punctuation.bracket
["⌜" "⌝"] @punctuation.bracket
; What a glyph stands in front of rather than between: a unit, a label, a
; lifetime, and the run a spread takes apart.
["¤" "§" "⧖" "⁂"] @punctuation.special

["&" "|" "^" "~"] @operator
["∧" "∨" "⊕" "⊼" "⊽" "¬"] @operator
["+" "-" "×" "÷" "%" "⊞" "⊟" "⊠"] @operator
["«" "»" "↺" "↻"] @operator
["#" "⍴" "⧺" "++" "⌈" "⌊"] @operator
["↓" "↑" "↕" "⇕" "ⁿ"] @operator
["←" "⌖" "…" "?" "??" "†" "⇧" "⇩"] @operator
; Whether one number divides another, which is asked of two and also written in
; front of one.
["∣" "∤"] @operator
; Every comparison, including the two that ask whether one number divides
; another: what they have in common is the shape they are in, and `=` is in that
; shape as well as in the one that binds a name.
(comparison_expression
  ["=" "≠" "<" ">" "≤" "≥" "<=" ">=" "≅" "≇" "⪅" "⪆" "⪉" "⪊" "∣" "∤"] @operator)
; Failure, which is a value in an expression and a shape in a pattern, and the
; same word in both.
"⊥" @keyword

; -- modules ------------------------------------------------------------------

(module_import name: (identifier) @module)
(member_expression base: (identifier) @module)
(type module: (identifier) @module)
