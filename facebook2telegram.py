# coding=utf-8
"""
Last done:
changed error handling in postPhotosToChat,
loading only up to 10 photos when fetching posts from FB
"""
import ast                                      #Used for pages list in ini
import configparser                             #Used for loading configs
import json                                     #Used for tacking last dates
import logging                                  #Used for logging
from os import remove
from os import path
import sys                                      #Used for exiting the program
from time import sleep
from datetime import datetime                   #Used for date comparison
from urllib import request                      #Used for downloading media
import re                                       #Used for youtube url patterns

import telegram                                 #telegram-bot-python
from telegram import (InputMediaPhoto,
                    InputMediaVideo)            #format photo and vid galleries
from telegram.ext import Updater
from telegram.error import TelegramError        #Error handling
from telegram.error import InvalidToken         #Error handling
from telegram.error import BadRequest           #Error handling
from telegram.error import TimedOut             #Error handling
from telegram.error import NetworkError         #Error handling

import facebook                                 #facebook-sdk

import youtube_dl                               #youtube-dl
from youtube_dl import utils


#Global Variables

#Logging
logging.basicConfig(
    filename='facebook2telegram.log',
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)

#youtube-dl
ydl = youtube_dl.YoutubeDL({'outtmpl': '{%(id)s%(ext)s}'})

YT_PATTERN = r"(https:\/\/youtu\.be\/[A-z0-9]{11}|https:\/\/www\.youtube\.com\/watch\?v=[A-z0-9]{11})"

settings = {}
dir_path = None
settings_path = None
dates_path = None
graph = None
start_time = None
facebook_pages = None
last_posts_dates = {}
bot = None
updater = None
dispatcher = None
job_queue = None


def loadSettingsFile(filename):
    '''
    Loads the settings from the .ini file
    and stores them in global variables.
    Use example.botsettings.ini as an example.
    '''
    #Read config file
    config = configparser.SafeConfigParser()
    config.read(filename)

    global settings

    #Load config
    try:
        settings['locale'] = config.get('facebook', 'locale')
        settings['facebook_token'] = config.get('facebook', 'token')
        settings['facebook_pages'] = ast.literal_eval(
                                        config.get("facebook", "pages"))
        settings['facebook_refresh_rate'] = float(
                                        config.get('facebook', 'refreshrate'))
        settings['allow_status'] = config.getboolean('facebook', 'status')
        settings['allow_photo'] = config.getboolean('facebook', 'photo')
        settings['allow_video'] = config.getboolean('facebook', 'video')
        settings['allow_link'] = config.getboolean('facebook', 'link')
        settings['allow_shared'] = config.getboolean('facebook', 'shared')
        settings['allow_message'] = config.getboolean('facebook', 'message')
        settings['allow_event'] = config.getboolean('facebook', 'event')
        settings['telegram_token'] = config.get('telegram', 'token')
        settings['channel_id'] = config.get('telegram', 'channel')
        settings['admin_id'] = config.get('telegram', 'admin')

        print('Loaded settings:')
        print('Locale: ' + settings['locale'])
        if settings['admin_id']:
            print('Admin ID: ' + settings['admin_id'])
        print('Channel: ' + settings['channel_id'])
        print('Refresh rate: ' + str(settings['facebook_refresh_rate']))
        print('Allow Status: ' + str(settings['allow_status']))
        print('Allow Photo: ' + str(settings['allow_photo']))
        print('Allow Video: ' + str(settings['allow_video']))
        print('Allow Link: ' + str(settings['allow_link']))
        print('Allow Shared: ' + str(settings['allow_shared']))
        print('Allow Message: ' + str(settings['allow_message']))
        print('Allow Event: ' + str(settings['allow_event']))

    except configparser.NoSectionError:
        sys.exit('Fatal Error: Missing or invalid settings file.')

    except configparser.NoOptionError:
        sys.exit('Fatal Error: Missing or invalid option in settings file.')

    except ValueError:
        sys.exit('Fatal Error: Missing or invalid value in settings file.')

    except SyntaxError:
        sys.exit('Fatal Error: Syntax error in page list.')


def loadFacebookGraph(facebook_token):
    '''
    Initialize Facebook GraphAPI with the token loaded from the settings file
    '''
    global graph
    graph = facebook.GraphAPI(access_token=facebook_token, version='3.1')


def loadTelegramBot(telegram_token):
    '''
    Initialize Telegram Bot API with the token loaded from the settings file
    '''
    global bot
    global updater
    global dispatcher
    global job_queue

    try:
        bot = telegram.Bot(token=telegram_token)
    except InvalidToken:
       sys.exit('Fatal Error: Invalid Telegram Token')

    updater = Updater(token=telegram_token)
    dispatcher = updater.dispatcher
    job_queue = updater.job_queue


def parsePostDate(post):
    '''
    Converts 'created_time' str from a Facebook post to the 'datetime' format
    '''
    date_format = "%Y-%m-%dT%H:%M:%S+0000"
    post_date = datetime.strptime(post['created_time'], date_format)
    return post_date


class dateTimeEncoder(json.JSONEncoder):
    '''
    Converts the 'datetime' type to an ISO timestamp for the JSON dumper
    '''
    def default(self, o):
        if isinstance(o, datetime):
            serial = o.isoformat()  #Save in ISO format
            return serial

        raise TypeError('Unknown type')
        return json.JSONEncoder.default(self, o)


def dateTimeDecoder(pairs, date_format="%Y-%m-%dT%H:%M:%S"):
    '''
    Converts the ISO timestamp to 'datetime' type for the JSON loader
    '''
    d = {}

    for k, v in pairs:
        if isinstance(v, str):
            try:
                d[k] = datetime.strptime(v, date_format)
            except ValueError:
                d[k] = v
        else:
            d[k] = v

    return d


def loadDatesJSON(last_posts_dates, filename):
    '''
    Loads the .json file containing the latest post's date for every page
    loaded from the settings file to the 'last_posts_dates' dict
    '''
    with open(filename, 'r') as f:
        loaded_json = json.load(f, object_pairs_hook=dateTimeDecoder)

    print('Loaded JSON file.')
    return loaded_json


def dumpDatesJSON(last_posts_dates, filename):
    '''
    Dumps the 'last_posts_dates' dict to a .json file containing the
    latest post's date for every page loaded from the settings file.
    '''
    with open(filename, 'w') as f:
        json.dump(last_posts_dates, f,
                  sort_keys=True, indent=4, cls=dateTimeEncoder)

    print('Dumped JSON file.')
    return True


def getMostRecentPostsDates(facebook_pages, filename):
    '''
    Gets the date for the most recent post for every page loaded from the
    settings file. If there is a 'dates.json' file, load it. If not, fetch
    the dates from Facebook and store them in the 'dates.json' file.
    The .json file is used to keep track of the latest posts posted to
    Telegram in case the bot is restarted after being down for a while.
    '''
    print('Getting most recent posts dates...')

    global start_time
    global last_posts_dates

    last_posts = graph.get_objects(
                ids=facebook_pages,
                fields='name,posts.limit(1){created_time}')

    print('Trying to load JSON file...')

    try:
        last_posts_dates = loadDatesJSON(last_posts_dates, filename)

        for page in facebook_pages:
            if page not in last_posts_dates:
                print('Checking if page '+page+' went online...')

                try:
                    last_post = last_posts[page]['posts']['data'][0]
                    last_posts_dates[page] = parsePostDate(last_post)
                    print('Page: '+last_posts[page]['name']+' went online.')
                    dumpDatesJSON(last_posts_dates, filename)
                except KeyError:
                    print('Page '+page+' not found.')

        start_time = 0.0 #Makes the job run its callback function immediately

    except (IOError, ValueError):
        print('JSON file not found or corrupted, fetching latest dates...')

        for page in facebook_pages:
            try:
                last_post = last_posts[page]['posts']['data'][0]
                last_posts_dates[page] = parsePostDate(last_post)
                print('Checked page: '+last_posts[page]['name'])
            except KeyError:
                print('Page '+page+' not found.')

        dumpDatesJSON(last_posts_dates, filename)


def getDirectURLVideo(post_id):
    '''
    Get direct URL for the video using GraphAPI and the post's 'attachments{media{source}}'
    '''
    print('Getting direct URL...')
    video_post = graph.get_object(
            id=post_id,
            fields='attachments{media}')

    return video_post['attachments']['data'][0]["media"]['source']

def getVideoURLFromText(post_message):
    """
    Looking for a youtube video link in the message text
    """
    print("Hello from getVideoURLFromText")
    yt_pattern = r"(https:\/\/youtu\.be\/[A-z0-9]{11}|https:\/\/www\.youtube\.com\/watch\?v=[A-z0-9]{11})"
    yt1 = r"https:\/\/youtu\.be\/.{11}"
    yt2 = r"https:\/\/www\.youtube\.com\/watch\?v=.{11}"
    try:
        if re.findall(yt1, post_message):
            id = re.findall(r"https:\/\/youtu\.be\/([A-z0-9]{11})", post_message)
            id = str(id).strip("['").rstrip("']")
            video = "https://www.youtube.com/watch?v={}".format(id)
        else:
            video = re.findall(yt2, post_message)
            video = str(video).strip('[').rstrip(']')
        return video

    except Exception as e:
        print(e)
        return None

def processPostMessage(post_message):
    """
    Check if the message is >500 characters
    If it is, shorten it to 500 characters
    Ouput: a tuple of strings: read_more (empty if not shortened), post text
    """
    if len(post_message) > 500:
        post_message = post_message[:500]
        last_space = post_message.rfind(' ')
        post_message = post_message[:last_space]
        post_message += "..."
        return "\nЧитати далі:\n", post_message
    return "", post_message


def getDirectURLVideoYDL(URL):
    '''
    Get direct URL for the video using youtube-dl
    '''
    try:
        with ydl:
            result = ydl.extract_info(URL, download=False) #Just get the link

        #Check if it's a playlist
        if 'entries' in result:
            video = result['entries'][0]
        else:
            video = result

        return video['url']
    except youtube_dl.utils.DownloadError:
        print('youtube-dl failed to parse URL.')
        return None


def postPhotoToChat(post, read_more, post_url, post_message, bot, chat_id):
    '''
    Posts the post's picture with the appropriate caption.
    '''
    direct_link = post['full_picture']

    try:
        message = bot.send_photo(
            chat_id=chat_id,
            caption=post_message + '\n' + read_more + post_url,
            photo=direct_link)
        return message

    except (BadRequest, TimedOut) as e:
        '''If the picture can't be sent using its URL,
        it is downloaded locally and uploaded to Telegram.'''
        try:
            print(e)
            print('Sending by URL failed, downloading file...')
            request.urlretrieve(direct_link, dir_path+'/temp.jpg')
            print('Sending file...')
            with open(dir_path+'/temp.jpg', 'rb') as picture:
                message = bot.send_photo(
                    chat_id=chat_id,
                    photo=picture,
                    caption=post_message + '\n' + read_more + post_url)
            remove(dir_path+'/temp.jpg')   #Delete the temp picture
            return message

        except TimedOut:
            '''If there is a timeout, try again with a higher
            timeout value for 'bot.send_photo' '''
            print('File upload timed out, trying again...')
            print('Sending file...')
            with open(dir_path+'/temp.jpg', 'rb') as picture:
                message = bot.send_photo(
                    chat_id=chat_id,
                    photo=picture,
                    caption=post_message,
                    timeout=120)
            remove(dir_path+'/temp.jpg')   #Delete the temp picture
            return message

        except BadRequest:
            print('Could not send photo file, sending link...')
            bot.send_message(    #Send direct link as a message
                chat_id=chat_id,
                text=direct_link+'\n'+post_message)
            return message

def postPhotosToChat(post, gallery, bot, chat_id):
    '''
    Posts multiple photos from the post without a caption
    '''
    media = []
    for photo in gallery:
        url = photo['media']['image']['src']
        media.append(InputMediaPhoto(media=url))

    try:
        message = bot.sendMediaGroup(
            chat_id=chat_id,
            media=media)
        return message

    except TimedOut:
        '''If the picture can't be sent using its URL,
        it is downloaded locally and uploaded to Telegram.'''

        print('Sending by URL failed, downloading file...')
        request.urlretrieve(gallery[1]['media']['image']['src'],
                            dir_path+'/temp.jpg')
        print('Sending file...')
        with open(dir_path+'/temp.jpg', 'rb') as picture:
            message = bot.send_photo(
                chat_id=chat_id,
                photo=picture)
        remove(dir_path+'/temp.jpg')   #Delete the temp picture
        return message


    except BadRequest:
        print('Could not send photo file...')
        return False


def postVideoToChat(post, read_more, post_message, bot, chat_id):
    """
    This function tries to pass 3 different URLs to the Telegram API
    instead of downloading the video file locally to save bandwidth.

    *First option":  Direct video source
    *Second option": Direct video source from text
    *Third option":  Direct video source with smaller resolution
    "Fourth option": Download file locally for upload
    "Fifth option":  Send the video link
    """
    # If youtube link, post the link
    if post['status_type'] == 'shared_story' and ('youtube.com' in post['message']):
        print('Sending YouTube link...')
        bot.send_message(
            chat_id=chat_id,
            text=post_message + '\n' + read_more + post['permalink_url'])
        print("Sent as message")
    elif post['status_type'] == 'added_video':
        print('Sending Facebook video link...')
        direct_link = post['attachments']['data'][0]["media"]['source']
        if read_more:
            bot.send_message(
                chat_id=chat_id,
                text=post_message + '\n' + direct_link + '\n' + read_more + post['permalink_url'])
        else:
            bot.send_message(
                chat_id=chat_id,
                text=post_message + '\n' + direct_link)
        print("Sent as message")
    else:
        # if 'attachments' in post:
        #     direct_link = getDirectURLVideo(post['attachments']['data'][0]["media"]['source'])
        # else:
        direct_link = getDirectURLVideo(post['id'])

        try:
            message = bot.send_video(
                chat_id=chat_id,
                video=direct_link,
                caption=post_message + '\n' + post['permalink_url'])
            print("Sent by object id")
            return message

        except TelegramError:        #If the API can't send the video
            try:
                print("Looking for video url in the text...")
                video_link = getVideoURLFromText(post_message)
                print("Found video link", video_link)
                message=bot.sendVideo(
                    chat_id=chat_id,
                    video=video_link,
                    caption=post_message + '\n' + post['permalink_url'])
                print("Sent message to telegram")

                    # print('Could not post video, trying youtube-dl...')
                    # message = bot.send_video(
                    #     chat_id=chat_id,
                    #     video=getDirectURLVideoYDL(post['attachments']['data'][0]['url']),
                    #     caption=post_message + '\n' + post['permalink_url'])
                return message

            except TelegramError as e:
                try:
                    print('Could not post video, trying smaller res...')
                    message = bot.send_video(
                        chat_id=chat_id,
                        video=post['source'],
                        caption=post_message + '\n')
                    return message

                except TelegramError:    #If it still can't send the video
                    try:
                        print('Sending by URL failed, downloading file...')
                        request.urlretrieve(post['source'],
                                            dir_path+'/temp.mp4')
                        print('Sending file...')
                        with open(dir_path+'/temp.mp4', 'rb') as video:
                            message = bot.send_video(
                                chat_id=chat_id,
                                video=video,
                                caption=post_message,
                                timeout=120)
                        remove(dir_path+'/temp.mp4')   #Delete the temp video
                        return message
                    except NetworkError:
                        print('Could not post video, sending link...')
                        message = bot.send_message(#Send direct link as message
                            chat_id=chat_id,
                            text=post_message+'\n'+direct_link)
                        return message


def postLinkToChat(post, read_more, post_url, post_message, bot, chat_id):
    '''
    Checks if the post has a message with its link in it. If it does,
    it sends only the message. If not, it sends the link followed by the
    post's message.
    '''
    if 'http' in post_message:
        post_link = ''
    else:
        post_link = post['attachments']['data'][0]['url']

    bot.send_message(
        chat_id=chat_id,
        text=post_link+'\n'+post_message + '\n' + read_more + post_url)


def postEventToChat(post, post_message, bot, chat_id):
    '''
    Posts an event to chat as a link
    Message includes event name, date, time, location, and post text
    '''
    event_link = post['attachments']['data'][0]['url']
    event_id = post['id'].split("_")[1]
    event = graph.get_object(id=event_id,
                            fields='name,place,start_time')

    name = '*' + event['name'] + '*' + '\n'
    time = event['start_time'].split('T')
    datetime = "*{}, {}*\n".format(time[0], time[1][:5])
    if 'place' in event:
        venue = '*' + event['place']['name'] + '*' + '\n'
    else:
        venue = '*TBA*' + '\n'
    message =  name + venue + datetime + "-"*26 + '\n' + post_message

    bot.send_message(
        chat_id=chat_id,
        text= event_link + '\n' + message,
        parse_mode='Markdown')





def checkIfAllowedAndPost(post, bot, chat_id):
    '''
    Checks the type of the Facebook post and if it's allowed by the
    settings file, then calls the appropriate function for each type.
    '''
    #If it's a shared post, call this function for the parent post
    if 'parent_id' in post and settings['allow_shared']:
        print('This is a shared post.')
        parent_post = graph.get_object(
            id=post['parent_id'],
            fields='created_time,id,message,full_picture,story,\
                    status_type,parent_id,attachments{media,url,subattachments},\
                    permalink_url',
            locale=settings['locale'])
        print('Accessing parent post...')
        checkIfAllowedAndPost(parent_post, bot, chat_id)
        return True

    '''If there's a message in the post, and it's allowed by the
    settings file, store it in 'post_message', which will be passed to
    another function based on the post type.'''

    try:
        post_url = post['permalink_url']
    except KeyError:
        if post['status_type'] == 'added_photos':
            post_url = post['attachments']['data'][0]['url']
        else:
            post_url = ''

    if 'message' in post and settings['allow_message']:
        read_more, post_message = processPostMessage(post['message'])
    else:
        read_more, post_message = '', ''

    if post['status_type'] == 'added_photos' and settings['allow_photo']:
        if 'attachments{subattachments}' not in post:
            print('Posting photo...')
            media_message = postPhotoToChat(post, read_more, post_url, post_message, bot, chat_id)


            """Posting a post with multiple photos
            Caption goes a separate message since telegram doesn't allow
            a message with a photo gallery
            Telegram takes up to 10 photos in one post
            Therefore only up to 10 photos out of a post/album will be shared
            """
        else:
            if 'message' in post:
                print('Posting the gallery caption text...')
                bot.send_message(
                    chat_id=chat_id,
                    text=post_message + '\n' + read_more + post_url)
            all_photos = post['attachments']['data'][0]['subattachments']['data'][:10]
            print('Posting photo gallery...')
            media_message = postPhotosToChat(post, all_photos, bot, chat_id)
        return True

    elif (post['status_type'] == 'added_video' or (post['status_type'] == 'shared_story' and 'youtube.com' in post['message'])) and settings['allow_video']:
        print('Posting video...')
        try:
            read_more, post_message = processPostMessage(post['message'])
        except KeyError:
            read_more, post_message = "", ""
        media_message = postVideoToChat(post, read_more, post_message, bot, chat_id)
        return True
    elif post['status_type'] == 'mobile_status_update' and settings['allow_status']:
        print('Posting status...')
        try:
            bot.send_message(
                chat_id=chat_id,
                text=post_message + '\n' + read_more + post['permalink_url'])
            return True
        except KeyError:
            print('Message not found, posting story...')
            bot.send_message(
                chat_id=chat_id,
                text=post_url + '\n' + post['story'])
            return True
    elif post['status_type'] == 'shared_story' and settings['allow_link']:
        print('Posting link...')
        postLinkToChat(post, read_more, post_url, post_message, bot, chat_id)
        return True
    elif post['status_type'] == 'created_event' and settings['allow_event']:
        try:
            print('Posting event...')
            postEventToChat(post, post_message, bot, chat_id)
            return True
        except Exception as e:
            print("Couldn't share event:\n", e)
            return False

    else:
        print('This post has a status type of "{}", skipping...'.format(post['status_type']))
        return False




def postToChat(post, bot, chat_id):
    '''
    Calls another function for posting and checks if it returns True.
    '''
    if checkIfAllowedAndPost(post, bot, chat_id):
        print('Posted.')


def postNewPosts(new_posts_total, chat_id):
    global last_posts_dates
    new_posts_total_count = len(new_posts_total)

    #Distribute posts between Facebook checks
    if new_posts_total_count > 0:
        time_to_sleep = settings['facebook_refresh_rate']/new_posts_total_count
    else:
        time_to_sleep = 0

    print('Posting {} new posts to Telegram...'.format(new_posts_total_count))
    for post in new_posts_total:
        posts_page = post['page']
        print('Posting NEW post from page {}...'.format(posts_page))
        try:
            postToChat(post, bot, chat_id)
            last_posts_dates[posts_page] = parsePostDate(post)
            dumpDatesJSON(last_posts_dates, dates_path)
        except BadRequest:
            print('Error: Telegram could not send the message')
            #raise
            continue
        print('Waiting {} seconds before next post...'.format(time_to_sleep))
        sleep(int(time_to_sleep))


def getNewPosts(facebook_pages, pages_dict, last_posts_dates):
    #Iterate every page in the list loaded from the settings file
    new_posts_total = []
    for page in facebook_pages:
        try:
            print('Getting list of posts for page {}...'.format(
                                                    pages_dict[page]['name']))

            #List of last 25 posts for current page. Every post is a dict.
            posts_data = pages_dict[page]['posts']['data']

            #List of posts posted after "last posted date" for current page
            new_posts = list(filter(
                lambda post: parsePostDate(post) > last_posts_dates[page],
                posts_data))

            if not new_posts:
                print('No new posts for this page.')
                continue    #Goes to next iteration (page)
            else:
                print('Found {} new posts for this page.'.format(len(new_posts)))
                for post in new_posts: #For later identification
                    post['page'] = page
                new_posts_total = new_posts_total + new_posts
        #If 'page' is not present in 'pages_dict' returned by the GraphAPI
        except KeyError:
            print('Page not found.')
            continue
    print('Checked all pages.')

    #Sorts the list of new posts in chronological order
    new_posts_total.sort(key=lambda post: parsePostDate(post))
    print('Sorted posts by chronological order.')

    return new_posts_total


def periodicCheck(bot, job):
    '''
    Checks for new posts for every page in the list loaded from the
    settings file, posts them, and updates the dates.json file, which
    contains the date for the latest post posted to Telegram for every
    page.
    '''
    global last_posts_dates
    chat_id = job.context
    print('Accessing Facebook...')

    try:
        #Request to the GraphAPI with all the pages (list) and required fields
        pages_dict = graph.get_objects(
            ids=facebook_pages,
            fields='name,\
                    posts{\
                          created_time,id,type,message,full_picture,story,\
                          caption,parent_id,attachments{media,url,subattachments.limit(10)},status_type,\
                          permalink_url}',
            locale=settings['locale'])

        #If there is an admin chat ID in the settings file
        if settings['admin_id']:
            try:
                #Sends a message to the bot Admin confirming the action
                bot.send_message(
                    chat_id=settings['admin_id'],
                    text='Successfully fetched Facebook posts.')

            except TelegramError:
                print('Admin ID not found.')
                print('Successfully fetched Facebook posts.')

        else:
            print('Successfully fetched Facebook posts.')

    #Error in the Facebook API
    except facebook.GraphAPIError as e:
        print('Error: Could not get Facebook posts.')
        print(e)
        '''
        TODO: 'get_object' for every page individually, due to a bug
        in the Graph API that makes some pages return an OAuthException 1,
        which in turn doesn't allow the 'get_objects' method return a dict
        that has only the working pages, which is the expected behavior
        when one or more pages in 'facbeook_pages' are offline. One possible
        workaround is to create an Extended Page Access Token instad of an
        App Token, with the downside of having to renew it every two months.
        '''
        return

    new_posts_total = getNewPosts(facebook_pages, pages_dict, last_posts_dates)

    print('Checked all posts. Next check in '
          +str(settings['facebook_refresh_rate'])
          +' seconds.')

    postNewPosts(new_posts_total, chat_id)

    if new_posts_total:
        print('Posted all new posts.')
    else:
        print('No new posts.')


def createCheckJob(bot):
    '''
    Creates a job that periodically calls the 'periodicCheck' function
    '''
    job_queue.run_repeating(periodicCheck, settings['facebook_refresh_rate'],
                            first=start_time, context=settings['channel_id'])
    print('Job created.')
    if settings['admin_id']:
        try:
            bot.send_message(
                chat_id=settings['admin_id'],
                text='Bot Started.')
        except TelegramError:
            print('Admin ID not found.')
            print('Bot Started.')


def error(bot, update, error):
    logger.warn('Update "{}" caused error "{}"'.format(update, error))


def main():
    global facebook_pages
    global dir_path
    global settings_path
    global dates_path

    dir_path = path.dirname(path.realpath(__file__))
    settings_path = dir_path+'/botsettings.ini'
    dates_path = dir_path+'/dates.json'

    loadSettingsFile(settings_path)
    loadFacebookGraph(settings['facebook_token'])
    loadTelegramBot(settings['telegram_token'])
    facebook_pages = settings['facebook_pages']

    getMostRecentPostsDates(facebook_pages, dates_path)

    createCheckJob(bot)

    #Log all errors
    dispatcher.add_error_handler(error)

    updater.start_polling()

    updater.idle()


if __name__ == '__main__':
    main()
