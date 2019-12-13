#!/usr/bin/env python3
""" Create Pull Request """
import json
import os
import random
import string
import sys
import time
from git import Repo
from github import Github, GithubException


def get_github_event(github_event_path):
    with open(github_event_path) as f:
        github_event = json.load(f)
    if bool(os.environ.get("DEBUG_EVENT")):
        print(os.environ["GITHUB_EVENT_NAME"])
        print(json.dumps(github_event, sort_keys=True, indent=2))
    return github_event


def get_head_short_sha1(repo):
    return repo.git.rev_parse("--short", "HEAD")


def get_random_suffix(size=7, chars=string.ascii_lowercase + string.digits):
    return "".join(random.choice(chars) for _ in range(size))


def remote_branch_exists(repo, branch):
    for ref in repo.remotes.origin.refs:
        if ref.name == ("origin/%s" % branch):
            return True
    return False


def get_author_default(event_name, event_data):
    if event_name == "push":
        email = "{head_commit[author][email]}".format(**event_data)
        name = "{head_commit[author][name]}".format(**event_data)
    else:
        email = os.environ["GITHUB_ACTOR"] + "@users.noreply.github.com"
        name = os.environ["GITHUB_ACTOR"]
    return email, name


def get_repo_url(token, github_repository):
    return "https://x-access-token:%s@github.com/%s" % (token, github_repository)


def checkout_branch(git, remote_exists, branch):
    if remote_exists:
        print("Checking out branch '%s'" % branch)
        git.stash("--include-untracked")
        git.checkout(branch)
        try:
            git.stash("pop")
        except BaseException:
            git.checkout("--theirs", ".")
            git.reset()
    else:
        print("Creating new branch '%s'" % branch)
        git.checkout("HEAD", b=branch)


def push_changes(git, token, github_repository, branch, commit_message):
    git.add("-A")
    print("author_name=%s, author_email=%s, committer_name=%s, committer_email=%s" % (
        author_name, author_email, committer_name, committer_email))
    git.commit(message=commit_message, author_name=author_name, author_email=author_email,
               committer_name=committer_name,
               committer_email=committer_email)
    repo_url = get_repo_url(token, github_repository)
    return git.push("-f", repo_url, f"HEAD:refs/heads/{branch}")


def cs_string_to_list(str):
    # Split the comma separated string into a list
    l = [i.strip() for i in str.split(",")]
    # Remove empty strings
    return list(filter(None, l))


def create_project_card(github_repo, project_name, project_column_name, pull_request):
    # Locate the project by name
    project = None
    for project_item in github_repo.get_projects("all"):
        if project_item.name == project_name:
            project = project_item
            break

    if not project:
        print("::warning::Project not found. Unable to create project card.")
        return

    # Locate the column by name
    column = None
    for column_item in project.get_columns():
        if column_item.name == project_column_name:
            column = column_item
            break

    if not column:
        print("::warning::Project column not found. Unable to create project card.")
        return

    # Create a project card for the pull request
    column.create_card(content_id=pull_request.id, content_type="PullRequest")
    print(
        "Added pull request #%d to project '%s' under column '%s'"
        % (pull_request.number, project.name, column.name)
    )


def process_event(github_token, github_repository, repo, branch, base):
    # Fetch optional environment variables with default values
    commit_message = os.getenv(
        "COMMIT_MESSAGE", "Auto-committed changes by create-pull-request action"
    )
    title = os.getenv(
        "PULL_REQUEST_TITLE", "Auto-generated by create-pull-request action"
    )
    body = os.getenv(
        "PULL_REQUEST_BODY",
        "Auto-generated pull request by "
        "[create-pull-request](https://github.com/peter-evans/create-pull-request) GitHub Action",
    )
    # Fetch optional environment variables with no default values
    pull_request_labels = os.environ.get("PULL_REQUEST_LABELS")
    pull_request_assignees = os.environ.get("PULL_REQUEST_ASSIGNEES")
    pull_request_milestone = os.environ.get("PULL_REQUEST_MILESTONE")
    pull_request_reviewers = os.environ.get("PULL_REQUEST_REVIEWERS")
    pull_request_team_reviewers = os.environ.get("PULL_REQUEST_TEAM_REVIEWERS")
    project_name = os.environ.get("PROJECT_NAME")
    project_column_name = os.environ.get("PROJECT_COLUMN_NAME")

    # Push the local changes to the remote branch
    print("Pushing changes to 'origin/%s'" % branch)
    push_result = push_changes(
        repo.git, github_token, github_repository, branch, commit_message
    )
    print(push_result)

    # Create the pull request
    github_repo = Github(github_token).get_repo(github_repository)
    try:
        pull_request = github_repo.create_pull(
            title=title, body=body, base=base, head=branch
        )
        print(
            "Created pull request #%d (%s => %s)" % (pull_request.number, branch, base)
        )
    except GithubException as e:
        if e.status == 422:
            # Format the branch name
            head_branch = "%s:%s" % (github_repository.split("/")[0], branch)
            # Get the pull request
            pull_request = github_repo.get_pulls(
                state="open", base=base, head=head_branch
            )[0]
            print(
                "Updated pull request #%d (%s => %s)"
                % (pull_request.number, branch, base)
            )
        else:
            print(str(e))
            sys.exit(1)

    # Set the output variables
    os.system("echo ::set-env name=PULL_REQUEST_NUMBER::%d" % pull_request.number)
    os.system("echo ::set-output name=pr_number::%d" % pull_request.number)

    # Set labels, assignees and milestone
    if pull_request_labels is not None:
        print("Applying labels '%s'" % pull_request_labels)
        pull_request.as_issue().edit(labels=cs_string_to_list(pull_request_labels))
    if pull_request_assignees is not None:
        print("Applying assignees '%s'" % pull_request_assignees)
        pull_request.as_issue().edit(
            assignees=cs_string_to_list(pull_request_assignees)
        )
    if pull_request_milestone is not None:
        print("Applying milestone '%s'" % pull_request_milestone)
        milestone = github_repo.get_milestone(int(pull_request_milestone))
        pull_request.as_issue().edit(milestone=milestone)

    # Set pull request reviewers
    if pull_request_reviewers is not None:
        print("Requesting reviewers '%s'" % pull_request_reviewers)
        try:
            pull_request.create_review_request(
                reviewers=cs_string_to_list(pull_request_reviewers)
            )
        except GithubException as e:
            # Likely caused by "Review cannot be requested from pull request
            # author."
            if e.status == 422:
                print("Requesting reviewers failed - %s" % e.data["message"])

    # Set pull request team reviewers
    if pull_request_team_reviewers is not None:
        print("Requesting team reviewers '%s'" % pull_request_team_reviewers)
        pull_request.create_review_request(
            team_reviewers=cs_string_to_list(pull_request_team_reviewers)
        )

    # Create a project card for the pull request
    if project_name is not None and project_column_name is not None:
        try:
            create_project_card(
                github_repo, project_name, project_column_name, pull_request
            )
        except GithubException as e:
            # Likely caused by "Project already has the associated issue."
            if e.status == 422:
                print(
                    "Create project card failed - %s" % e.data["errors"][0]["message"]
                )


# Fetch environment variables
github_token = os.environ["GITHUB_TOKEN"]
github_repository = os.environ["GITHUB_REPOSITORY"]
github_ref = os.environ["GITHUB_REF"]
event_name = os.environ["GITHUB_EVENT_NAME"]
# Get the JSON event data
event_data = get_github_event(os.environ["GITHUB_EVENT_PATH"])

# Get the default for author email and name
author_email, author_name = get_author_default(event_name, event_data)
# Set author name and email overrides
author_name = os.getenv("COMMIT_AUTHOR_NAME", author_name)
author_email = os.getenv("COMMIT_AUTHOR_EMAIL", author_email)
# Set committer name and email overrides
committer_name = os.getenv("COMMITTER_NAME", author_name)
committer_email = os.getenv("COMMITTER_EMAIL", author_email)

# Set the repo to the working directory
repo = Repo(os.getcwd())
# Set git environment. This will not persist after the action completes.
print("Configuring git author as '%s <%s>'" % (author_name, author_email))
print("Configuring git committer as '%s <%s>'" % (committer_name, committer_email))
repo.git.update_environment(
    GIT_AUTHOR_NAME=author_name,
    GIT_AUTHOR_EMAIL=author_email,
    GIT_COMMITTER_NAME=committer_name,
    GIT_COMMITTER_EMAIL=committer_email,
)

# Fetch/Set the branch name
branch_prefix = os.getenv("PULL_REQUEST_BRANCH", "create-pull-request/patch")
# Fetch an optional base branch override
base_override = os.environ.get("PULL_REQUEST_BASE")

# Set the base branch
if base_override is not None:
    base = base_override
    print("Overriding the base with branch '%s'" % base)
    checkout_branch(repo.git, True, base)
elif github_ref.startswith("refs/pull/"):
    # Check the PR is not raised from a fork of the repository
    head_repo = "{pull_request[head][repo][full_name]}".format(**event_data)
    if head_repo != github_repository:
        print(
            "::warning::Pull request was raised from a fork of the repository. "
            + "Limitations on forked repositories have been imposed by GitHub Actions. "
            + "Unable to continue. Exiting."
        )
        sys.exit()
    # Switch to the merging branch instead of the merge commit
    base = os.environ["GITHUB_HEAD_REF"]
    print(
        "Removing the merge commit by switching to the pull request head branch '%s'"
        % base
    )
    checkout_branch(repo.git, True, base)
elif github_ref.startswith("refs/heads/"):
    base = github_ref[11:]
    print("Currently checked out base assumed to be branch '%s'" % base)
else:
    print(
        f"::warning::Currently checked out ref '{github_ref}' is not a valid base for a pull request. "
        + "Unable to continue. Exiting."
    )
    sys.exit()

# Skip if the current branch is a PR branch created by this action.
# This may occur when using a PAT instead of GITHUB_TOKEN because
# a PAT allows workflow actions to trigger further events.
if base.startswith(branch_prefix):
    print("Branch '%s' was created by this action. Skipping." % base)
    sys.exit()

# Fetch an optional environment variable to determine the branch suffix
branch_suffix = os.getenv("BRANCH_SUFFIX", "short-commit-hash")
if branch_suffix == "short-commit-hash":
    # Suffix with the short SHA1 hash
    branch = "%s-%s" % (branch_prefix, get_head_short_sha1(repo))
elif branch_suffix == "timestamp":
    # Suffix with the current timestamp
    branch = "%s-%s" % (branch_prefix, int(time.time()))
elif branch_suffix == "random":
    # Suffix with the current timestamp
    branch = "%s-%s" % (branch_prefix, get_random_suffix())
elif branch_suffix == "none":
    # Fixed branch name
    branch = branch_prefix
else:
    print("Branch suffix '%s' is not a valid value." % branch_suffix)
    sys.exit(1)

# Output head branch
print("Pull request branch to create/update set to '%s'" % branch)

# Check if the determined head branch exists as a remote
remote_exists = remote_branch_exists(repo, branch)
if remote_exists:
    print(
        "Pull request branch '%s' already exists as remote branch 'origin/%s'"
        % (branch, branch)
    )
    if branch_suffix == "short-commit-hash":
        # A remote branch already exists for the HEAD commit
        print(
            "Pull request branch '%s' already exists for this commit. Skipping."
            % branch
        )
        sys.exit()
    elif branch_suffix in ["timestamp", "random"]:
        # Generated branch name collision with an existing branch
        print(
            "Pull request branch '%s' collided with a branch of the same name. Please re-run."
            % branch
        )
        sys.exit(1)

# Checkout branch
checkout_branch(repo.git, remote_exists, branch)

# Check if there are changes to pull request
if remote_exists:
    print(
        "Checking for local working copy changes indicating a "
        + "diff with existing pull request branch 'origin/%s'" % branch
    )
else:
    print(
        "Checking for local working copy changes indicating a "
        + "diff with base 'origin/%s'" % base
    )

if repo.is_dirty() or len(repo.untracked_files) > 0:
    print("Modified or untracked files detected.")
    process_event(github_token, github_repository, repo, branch, base)
else:
    print("No modified or untracked files detected. Skipping.")
