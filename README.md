# Wyze-CLI

Connand-line app to easily manage some models of Wyze cameras (not all of them work). 

My scenarios are turning on/off motion detection based on [geofencing](https://owntracks.org/) and restarting my cameras once a day. Before, I was using IFTTT for that scenarios until 
[they got greedy](https://www.reddit.com/r/ifttt/comments/18p7pxa/webhooks_suddenly_stopped_working_requires_pro/).

## Examples

Running the following from a sh terminal will turn off motion detection for all cameras but for the ones named "Outside Shed", "Outside Kitchen" or "Front Door Bell", and print "failed x operation(s)" with x a number if some cameras were unresponsive (which isn't uncommon - rebooting cameras once a day helps some):
```
python3 app.py --action motion_alarm_off --log-level=INFO --exclude='Outside Shed,Outside Kitchen,Front Door Bell' || echo "failed $? operation(s)"
```

Note: no error is raised when specifying as exclude or include filters cameras that don't exist. 


Other examples
```
python3 app.py --action motion_alarm_on --exclude "Front Door Bell"
python3 app.py --action restart --include "Kitchen pan"
```

# Getting the credentials

Needed are: wyze e-mail and password, and also api id and api key. 
The latter two can be created [here](https://developer-api-console.wyze.com/#/apikey/view)


# Running the app

## From command line:

`python3 app.py --action restart --wyze-email 'Wyze email' --wyze-password 'Wyze password' --wyze-key-id 'Wyze key id' --wyze-api-key 'Wyze api key'`

## From command line with env variables

Pro of env vars: these don't show up when doing `ps`

```
export WYZE_EMAIL=your@email.com
export WYZE_PASSWORD=some_strong_password
export WYZE_KEY_ID=the_key_id
export WYZE_API_KEY=the_api_key

python3 app.py --action restart --log-level=INFO
```

Note: not all cameras support all actions. 

## From docker

This sets a docker container to restart all Wyze cameras but 3 of them:

```
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
