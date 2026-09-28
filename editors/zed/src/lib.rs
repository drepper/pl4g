//! The PL4G extension for Zed.
//!
//! Zed reads the grammar, the queries and the language's settings from the files
//! beside this one; what it cannot read from a file is which command starts the
//! language server, so that is what this answers and it is the whole of the code.
//!
//! The server is the compiler: `pypl4g lsp`.  Which `pypl4g` is a question worth
//! asking carefully -- what is wanted is the compiler of the project the file
//! belongs to, since a program is checked by the compiler it will be built with.

use zed_extension_api::{self as zed, settings::LspSettings, LanguageServerId, Result};

/// Where the compiler is in a project of this language.
const IN_THE_PROJECT: &str = "bin/pypl4g";

/// And what it is called where it was installed rather than cloned.
const ON_THE_PATH: &str = "pypl4g";

/// The word that puts the compiler into the mode this extension talks to.
const SPEAKS_THE_PROTOCOL: &str = "lsp";

struct Pl4gExtension;

impl zed::Extension for Pl4gExtension {
    fn new() -> Self {
        Self
    }

    /// The command that starts the server, looked for in three places.
    ///
    /// What the settings name, first: a reader who has said which compiler to
    /// use has answered the question.  Then the one in the project, which is
    /// what is wanted whenever the file is in a checkout of the language --
    /// `bin/pypl4g` runs the compiler out of the tree it is in, so a program is
    /// checked by the compiler it is written beside.  Then whatever is on the
    /// path, for a file that belongs to no such tree.
    fn language_server_command(
        &mut self,
        language_server_id: &LanguageServerId,
        worktree: &zed::Worktree,
    ) -> Result<zed::Command> {
        let settings = LspSettings::for_worktree(language_server_id.as_ref(), worktree).ok();
        let stated = settings
            .and_then(|found| found.binary)
            .and_then(|binary| binary.path);
        let command = match stated {
            Some(path) => path,
            None => in_the_worktree(worktree)
                .or_else(|| worktree.which(ON_THE_PATH))
                .ok_or_else(|| {
                    format!(
                        "no {IN_THE_PROJECT} in this project and no {ON_THE_PATH} on the path; \
                         say where the compiler is under `lsp.pl4g.binary.path`"
                    )
                })?,
        };
        Ok(zed::Command {
            command,
            args: vec![SPEAKS_THE_PROTOCOL.to_string()],
            env: worktree.shell_env(),
        })
    }
}

/// The compiler of the project the file is in, where the file is in one.
///
/// Asked by reading the file rather than by looking for it: what an extension
/// may do to a worktree is read it, which is enough to tell a checkout of this
/// language from any other directory.
fn in_the_worktree(worktree: &zed::Worktree) -> Option<String> {
    worktree.read_text_file(IN_THE_PROJECT).ok()?;
    Some(format!("{}/{IN_THE_PROJECT}", worktree.root_path()))
}

zed::register_extension!(Pl4gExtension);
