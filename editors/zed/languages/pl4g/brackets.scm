; Which brackets match which, for the highlight that shows the other end.
;
; The shapes rather than the characters: what closes a `⟦` is the `⟧` of the same
; array, which the tree knows and a search of the text would have to work out.

(parenthesized_expression "(" @open ")" @close)
(parameter_list "(" @open ")" @close)
(call_expression "(" @open ")" @close)
(explicit_block "{" @open "}" @close)
(array_literal "⟦" @open "⟧" @close)
(tuple_literal "〈" @open "〉" @close)
(set_literal "⸨" @open "⸩" @close)
(dictionary_literal "⸨" @open "⸩" @close)
(lifted_expression "⌜" @open "⌝" @close)
(attribute_list "@[" @open "]" @close)
(index_expression "⸨" @open "⸩" @close)
(element_expression "⟦" @open "⟧" @close)
