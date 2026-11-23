# Wyze-CLI

Command-line app to easily manage some models of Wyze cameras. Note: not all Wyze cameras support all actions. 

My scenarios are turning on/off motion detection based on [geofencing](https://owntracks.org/), and restarting my cameras once a day. 

I was using IFTTT to do that until 
[they got greedy](https://www.reddit.com/r/ifttt/comments/18p7pxa/webhooks_suddenly_stopped_working_requires_pro/).

## Requirements

Python 3.10 or later installed.

## Examples

Running the following from a sh terminal will turn off motion detection for all cameras but for the ones named "Outside Shed", "Outside Kitchen" or "Front Door Bell", and print "failed x operation(s)" with x a number if some cameras were unresponsive (which isn't uncommon - rebooting cameras once a day helps some):
```bash
python3 app.py --action motion_alarm_off --log-level=INFO --exclude='Outside Shed,Outside Kitchen,Front Door Bell' || echo "failed $? operation(s)"
```

Note: camera names are matched case sensitively by default - use `--case-insensitive` to relax that. Macs are always matched case insensitively. No error is raised when specifying cameras that don't exist as exclude or include filters. 

## Exit codes

The exit code is the number of cameras the action failed on, capped at 254, so a cron job can tell a clean run (0) from a partial one. 255 means the app itself failed - bad or expired credentials, a malformed regex, or Wyze being unreachable - and no camera was acted on in that case.

If Wyze rejects the access token partway through, the app gets a new one and retries the camera that failed. If it cannot get a usable token at all, it stops there instead of sending the same doomed request to every remaining camera; the cameras it skipped are still counted as failed, so the exit code keeps describing what did not work.


Other examples
```bash
python3 app.py --action motion_alarm_on --exclude "Front Door Bell"
python3 app.py --action restart --include "Kitchen pan"
```

# Getting the credentials

Needed: wyze e-mail, password, api id, and api key. 
The latter two can be created [here](https://developer-api-console.wyze.com/#/apikey/view)


# Running the app

## From command line:

```bash
python3 app.py --action restart --wyze-email 'Wyze email' --wyze-password 'Wyze password' --wyze-key-id 'Wyze key id' --wyze-api-key 'Wyze api key'`
```

## From command line with env variables

Pro of env vars: these don't show up when doing `ps`

```bash
export WYZE_EMAIL=your@email.com
export WYZE_PASSWORD=some_strong_password
export WYZE_KEY_ID=the_key_id
export WYZE_API_KEY=the_api_key

python3 app.py --action restart --log-level=INFO
```


## From docker

This sets a docker container to restart all Wyze cameras but 3 of them:

```bash
sudo docker run \
  --name wyze-restart -d \
  -e WYZE_EMAIL=your@email.com \
  -e WYZE_PASSWORD=some_strong_password \
  -e WYZE_KEY_ID=the_key_id \
  -e WYZE_API_KEY=the_api_key \
  vdbg/wyze-cli --action restart --exclude="Outside Shed,Outside Kitchen,Front Door Bell"
```

This verifies the setup is correct (proper credentials, etc.):
`sudo docker container logs wyze-restart`

If setup not correct, run this and restart at first step:
`sudo docker container rm wyze-restart`

Once setup is confirmed correct, this will restart the Wyze cameras:
`sudo docker container start wyze-restart`

Multiple docker containers can be created from the same image; in my case I have 3 of them: 
wyze-restart, wyze-monitor-on, wyze-monitor-off.

### Keeping the access token between runs

The app caches the Wyze access token in `wyze_token.json` next to `app.py`, which inside a container is `/app/wyze_token.json`. That survives `docker container start` of the same container, but it is gone as soon as the container is recreated, and every fresh container then pays for a password login - which Wyze throttles. Mount a volume and point `WYZE_TOKEN_FILE` at it to keep the cache, and give each container its own file (as below) if several of them share one volume:

```bash
sudo docker run \
  --name wyze-restart -d \
  -v wyze_token:/token \
  -e WYZE_TOKEN_FILE=/token/restart_token.json \
  -e WYZE_EMAIL=your@email.com \
  -e WYZE_PASSWORD=some_strong_password \
  -e WYZE_KEY_ID=the_key_id \
  -e WYZE_API_KEY=the_api_key \
  vdbg/wyze-cli --action restart --exclude="Outside Shed,Outside Kitchen,Front Door Bell"
```

## Running the tests

From the repository root:

```bash
python3 -m unittest discover -s tests
```

The tests run `app.py` against a small stand-in for `wyze_sdk` in `tests/test_app.py`,
so they need no network, no Wyze credentials and no extra package - plain `unittest`
is enough (the sdk itself is only needed to run the app for real). They cover the
include/exclude and mac filtering rules, the token caching, and the exit codes.

## Known issues

### Expiring API key

The expiration date for the Wyze API key is a lie. Sometimes Wyze will silently delete
the key with no warning or notification. The symptom will be a line of log similar to:
```text
root - CRITICAL - 400 Client Error: Bad Request for url: https://auth-prod.api.wyze.com/api/user/login
```

Fix: head to [this page](https://developer-api-console.wyze.com/#/apikey/view) to confirm the api key
has disappeared, create a new one, and update the `WYZE_KEY_ID` and `WYZE_API_KEY` settings.

### Missing cameras

If a camera is missing when running `--action list`, try running `--action list_all`. If the camera appears when doing the latter, then 
send a pull request to update https://github.com/vdbg/wyze-sdk/tree/add_camera_models