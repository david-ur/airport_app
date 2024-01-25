import argparse

from raya.application_base import RayaApplicationBase
from raya.controllers.fleet_controller import FleetController
from raya.controllers.ui_controller import UIController
from raya.controllers.navigation_controller import NavigationController, POSITION_UNIT, ANGLE_UNIT
from raya.controllers.leds_controller import LedsController
from raya.controllers.sound_controller import SoundController
from raya.exceptions import *
from raya.enumerations import *
from raya.tools.filesystem import open_file, check_file_exists, create_dat_folder, resolve_path


import eyed3
import os
from time import time
import asyncio

# Import VR libraries and create a text to speech client
from google.cloud import texttospeech
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = '/opt/raya_os/rayadevel/apps/airport_app/src/client_service_key.json'
text_to_speech_client = texttospeech.TextToSpeechClient()

# Status IDs pointing to obstacles detection
OBSTACLE_DICT = {'Waiting obstacle to move' : 9,
                 'Waiting' : 10,
                 'Obstacle detected' : 167,
                 'Moving linear' : 30,
                 }

PATH_OBSTRUCTION_DICT = {'Couldnt Compute path to pose' : 116}
AUDIO_PATH = f'dat:tts_audio' 


class RayaApplication(RayaApplicationBase):

    async def setup(self):
        
        # Enable controlles
        self.nav: NavigationController = await self.enable_controller('navigation')        
        self.fleet: FleetController = await self.enable_controller('fleet')
        self.leds: LedsController = await self.enable_controller('leds')
        self.UI: UIController = await self.enable_controller('ui')
        self.sound: SoundController = await self.enable_controller('sound')
        
        # Set variables
        self.i = 0
        self.map_name = 'airport_gallery_rebuilt'
    

        self.available_locations = [
            {'x': 207.0, 'y': 847.0, 'angle': -44.0, 'name': 'נקודה רחוקה - גלריה', 'default_camera': 'nav_bottom'},
            {'x': 346.0, 'y': 716.0, 'angle': -44.0, 'name': 'אמצע מרחב פתוח', 'default_camera': 'nav_bottom'},
            {'x': 434.0, 'y': 715.0, 'angle': -44.0, 'name': 'פח1', 'default_camera': 'nav_bottom'},
            {'x': 492.0, 'y': 590.0, 'angle': -44.0, 'name': 'נקודה אחרי הפח', 'default_camera': 'nav_bottom'},
            {'x': 339.0, 'y': 353.0, 'angle': 27.4, 'name': 'פח 2', 'default_camera': 'nav_bottom'},
            {'x': 419.0, 'y': 244.0, 'angle': 27.4, 'name': 'נקודה רחוקה - אחרי הדלתות', 'default_camera': 'nav_bottom'},
            {'x': 511.0, 'y': 336.0, 'angle': 27.4, 'name': 'דלתות', 'default_camera': 'nav_bottom'},
            {'x': 658.0, 'y': 453.0, 'angle': 27.4, 'name': 'חדר בקרה - חוץ', 'default_camera': 'nav_bottom'}
        ]
        self.home_position = { 'x': 615.0, 'y': 551.0, 'angle': 90.8 }
        self.screen_list= [
        'https://fms-s3-dev.s3.eu-central-1.amazonaws.com/airport/%D7%A4%D7%A8%D7%95%D7%99%D7%A7%D7%98+%D7%A0%D7%AA%D7%91%D7%92+-+%D7%9E%D7%A1%D7%9B%D7%99%D7%9D/1.png',
        'https://fms-s3-dev.s3.eu-central-1.amazonaws.com/airport/%D7%A4%D7%A8%D7%95%D7%99%D7%A7%D7%98+%D7%A0%D7%AA%D7%91%D7%92+-+%D7%9E%D7%A1%D7%9B%D7%99%D7%9D/2.png',
        'https://fms-s3-dev.s3.eu-central-1.amazonaws.com/airport/%D7%A4%D7%A8%D7%95%D7%99%D7%A7%D7%98+%D7%A0%D7%AA%D7%91%D7%92+-+%D7%9E%D7%A1%D7%9B%D7%99%D7%9D/3.png',
        'https://fms-s3-dev.s3.eu-central-1.amazonaws.com/airport/%D7%A4%D7%A8%D7%95%D7%99%D7%A7%D7%98+%D7%A0%D7%AA%D7%91%D7%92+-+%D7%9E%D7%A1%D7%9B%D7%99%D7%9D/4.png',
        'https://fms-s3-dev.s3.eu-central-1.amazonaws.com/airport/%D7%A4%D7%A8%D7%95%D7%99%D7%A7%D7%98+%D7%A0%D7%AA%D7%91%D7%92+-+%D7%9E%D7%A1%D7%9B%D7%99%D7%9D/5.png]',
        ]
        self.current_screen_index = 0
        
        self.final_task_status = FLEET_FINISH_STATUS.SUCCESS
        self.final_task_message = 'Application finished successfully'
        LedsController
        self.navigation_tries = 1
        self.language = 'HEBREW'
        
        
        # localize
        self.log.info((f'Setting map: {self.map_name}. '
                       'Waiting for the robot to get localized'))
        robot_localized = await self.nav.set_map(
                map_name=self.map_name, 
                wait_localization=True, 
                timeout=45.0,
            )
        if not robot_localized:
            self.log.error(f'Robot couldn\'t localize itself')
            self.finish_app()
        self.status = await self.nav.get_status()
        self.log.info(f'status: {self.status}')
        
        #Thread loops
        self.create_task(name='ui loop', afunc=self.custom_loop, interval=10, fn=self.show_ui)
        self.log.info('loop started')
        #Led setup
        
        await self.turn_on_leds(rep_time = 0, animation='SAFETY_SCANNING_COLORS', color='#000101',speed=1)            
        
        # Setup callbacks
        self.fleet.set_msgs_from_fleet_callback(callback_async=self.fleet_cb)

        # Download relevant sounds
        self.log.info('starting downloading voices')
        self.download_all_voices()
        self.log.info('All voices Downloaded')



    async def loop(self):

        # Handle navigation

        await self.preform_navigation(self.available_locations[self.i]['x'], self.available_locations[self.i]['y'], self.available_locations[self.i]['angle'])
        
        # Leds
        # await self.turn_on_leds(self.leds_list[self.i])
        
        
        #Open Camera
        await self.turn_on_leds(rep_time = 0, animation='SAFETY_SCANNING_COLORS', color='#000102',speed=1)            

        await self.show_camera(self.available_locations[self.i]['name'], self.available_locations[self.i]['default_camera'])
        
        
        #Finish the app
        self.i=self.i+1
        if self.i > 2:
            self.finish_app()
        

    async def finish(self):
        # Finishing instructions
        await self.leds.turn_off_group(group='head')
        await self.return_home()
        await self.fleet.finish_task(
                task_id=self.fleet.task_id, 
                result=self.final_task_status,
                message=self.final_task_message
            )
        self.log.info(f'Airport app finished')
        
    async def show_ui(self):
        try:
            await self.UI.show_animation(
                back_button_text= '',
                url= self.screen_list[self.current_screen_index],
                custom_style= {
                    'background': {
                            'padding': 0
                    },
                    'image': {
                            'width': '100%',
                            'height': '100%'
                    },
                }
            )
            self.current_screen_index = 0 if self.current_screen_index == 4 else self.current_screen_index + 1
        except Exception as e:
            self.log.warn(f'UI cannot be displayed {e}')

    async def preform_navigation(self, x, y, angle):
        try:
            await self.nav.navigate_to_position(x=x,
                                                y=y, 
                                                angle=angle,
                                                pos_unit=POSITION_UNIT.PIXELS, 
                                                ang_unit=ANGLE_UNIT.DEGREES, 
                                                wait=True,
                                                callback_feedback_async = self.cb_nav_feedback,
                                                # callback_finish = self.cb_nav_finish
                                                )
            self.log.info(f'Naviged to {x}, {y}')
        except RayaException as e:
            self.log.warn(f'Navigation failed because {e}')
            self.finish_app()
        
        # # Obstacle management flow                                    
        # self.nav_errors = await self.check_navigation_errors()

    async def turn_on_leds(self, rep_time = 0, group = 'head', animation = 'MOTION_4', color = 'BLUE',speed = 5):
        try:
            await self.leds.animation(
                        group = group, 
                        color = color, 
                        animation = animation, 
                        speed = speed, 
                        # repetitions = int(0.3*rep_time) + 1,
                        repetitions=rep_time,
                        execution_control=LEDS_EXECUTION_CONTROL.OVERRIDE,
                        wait=True)
            
        except Exception as e:
            self.log.warn(f'Skipped leds, got exception {e}')

    async def show_camera(self, title, default_camera = 'nav_top'):
        try:
            fleet_response = await self.fleet.open_camera_stream(title=title, subtitle="", default_camera=default_camera, button_cancel_txt="not confirm", button_ok_txt="confirm") 
            self.log.info(fleet_response['data'])
            if fleet_response['data'] == 'confirm':
                await self.fleet.update_app_status(
                    task_id=self.fleet.task_id,
                    status=FLEET_UPDATE_STATUS.SUCCESS,
                    message='אזור מאושר בהצלחה ' + title
                )
            else:
                await self.fleet.update_app_status(
                    task_id=self.fleet.task_id,
                    status=FLEET_UPDATE_STATUS.ERROR,
                    message='אזור לא מאושר על ידי חפ"ק ' + title
                ) 
        except Exception as e:
            self.log.warn(f'Camera stream can not be opened because {e}')
            
            
    async def custom_loop(self, interval, fn):       
        while True:
            await fn()
            await asyncio.sleep(interval)
            
            
    async def fleet_cb(self, fleet_msg):
        if fleet_msg['command'] == 'stop_app':
            self.final_task_message = 'Application was stopped'
            self.finish_app()
            
            
    async def cb_nav_feedback(self,  error, error_msg, distance_to_goal, speed):    
        if not self.sound.is_playing() :
            if error==9: 
                self.log.info(f'{error}, ..... ,{error_msg}')
                await self.turn_on_leds(rep_time = 0, animation='MOTION_1', color='RED')
                await self.play_predefined_sound(recording_name= 'VOICE_PLEASE_MOVE_HEBREW', )
            
            # while(self.sound.is_playing()):
            if(error==30):
            #     await self.sleep(0.01)
                await self.turn_on_leds(rep_time = 0, animation='SAFETY_SCANNING_COLORS', color='#000101',speed=1)            

        # await self.turn_on_leds(rep_time = 0, animation='SAFETY_SCANNING_COLORS', color='#000101')            
    # def cb_nav_finish(self, error, error_msg):
    #     try:
    #         self.create_task(name='nav finish',afunc=self.async_cb_nav_finish,
    #                     error=error,
    #                     error_msg=error_msg,
    #                     )
    #     except Exception as e:
    #         print(f'Error in cb_nav_finish {e}')
    
    # async def check_navigation_errors(self):
    #         '''
    #         INPUTS:
    #             The function has no inputs

    #         OUTPUTS:
    #             True for navigation errors, False for no navigation errors
    #         '''

    #         # Check for obstacle messages
    #         for status in self.navigation_messages:
    #             if status in OBSTACLE_DICT.values() or status in PATH_OBSTRUCTION_DICT.values():
    #                 self.navigation_messages = []
    #                 self.last_navigation_failed = False

    #                 # If its not your first attempt, wait for a bit
    #                 if self.navigation_tries != 1:
    #                     await self.sleep(5)

    #                 # Navigation failed 1 to 3 times
    #                 if 1 <= self.navigation_tries <= 3:
    #                     await self.turn_on_leds(animation = 'MOTION_4', color = 'RED')  # Leds indication for navigation error
    #                     await self.play_predefined_sound(f'VOICE_PLEASE_MOVE_{self.language}', leds = False)
    #                     self.navigation_tries += 1

    #                 # Navigation failed 4 times
    #                 if self.navigation_tries == 4:
    #                     await self.turn_on_leds(animation = 'MOTION_4', color = 'RED')  # Leds indication for navigation error
    #                     # await self.play_predefined_sound(f'VOICE_ASKING_FLEET_FOR_HELP_{self.language}', leds = False)
    #                     await self.update_fleet("Hi, I'm facing difficulties. Can you come and help me?", status_type = FLEET_UPDATE_STATUS.WARNING)
                        
    #                     self.navigation_tries += 1

    #                 # Navigation failed 5 times
    #                 if self.navigation_tries == 5:
    #                     self.navigation_tries = 0 # Reset navigation tries
    #                     try:
    #                         await self.request_instruction(
    #                             title='I need help',
    #                             message="I ran into an obstacle. Can you help me remove it?",
    #                             timeout=60.0
    #                         )

    #                     # If a timeout occurs (the robot took too long to reach the cart), return home position, and finish the app
    #                     except RayaFleetTimeout:

    #                         # Cancel navigation if navigating
    #                         if self.nav.is_navigating():
    #                             await self.nav.cancel_navigation()

    #                         # Return home
    #                         await self.turn_on_leds(animation = 'MOTION_4', color = 'RED')  # Leds indication for navigation error
    #                         await self.play_predefined_sound(f'VOICE_COULDNT_REACH_DESTINATION_{self.language}', leds = False)
    #                         await self.return_home()

    #                         # Finish the app
    #                         self.last_navigation_failed = False
    #                         self.state = self.finish()
    #                         return True
                    
    #                 return True
            
    #         # No navigation errors found
    #         return False
        
    async def play_predefined_sound(self, recording_name, audio_type = 'mp3', leds = True):
        '''
        INPUTS:
            recording_name - the name of the recording ; str
        
        OUTPUTS:
            This function has no outputs. It plays a sound
        '''

        path = f'{AUDIO_PATH}/{recording_name}.{audio_type}'

        if audio_type == 'mp3': # TODO: Add something more robust than eyed3 package
            self.audio_duration = eyed3.load(resolve_path(path)).info.time_secs

        # if leds is True:
        #     try:
        #         await self.turn_on_leds(rep_time = self.audio_duration)
            
        #     except Exception as e:
        #         self.log.warn(f'Got exception {e} in text_to_speech method, skipping leds')

        try:
            await self.sound.play_sound(path = f'{AUDIO_PATH}/{recording_name}.{audio_type}')

        except Exception as e:
            self.log.warning(f'Skipped playing sound, got error {e}')   

    # Download a voice
    def download_voice(self, text, file_name, language = 'en-GB', name = 'en-GB-Neural2-B', audio_type = 'mp3', dynamic = False):
        '''
        INPUTS:
                text - A text for the robot to download
        
        OUTPUTS:
                This function doesn't return any outputs, it downloads 'text'
        '''

        # Get relevant path
        path = f'{AUDIO_PATH}/{file_name}.{audio_type}'

        # If the voice isnt downloaded already, download it
        if not check_file_exists(path) or dynamic is True: 

            self.log.info(f'Downloading audio: \'{path}\'')
            synthesized_input = texttospeech.SynthesisInput(text = text)
            voice = texttospeech.VoiceSelectionParams(
            language_code = language,
            name = name,
            ssml_gender = texttospeech.SsmlVoiceGender.MALE)

            audio_config = texttospeech.AudioConfig(audio_encoding = texttospeech.AudioEncoding.MP3)
            response = text_to_speech_client.synthesize_speech(input = synthesized_input, voice = voice, audio_config = audio_config)

            with open_file(path, 'wb') as gary_response:
                gary_response.write(response.audio_content)

        # Do nothing if the voice is already downloadaed
        # else:
        #     pass
        
    def download_all_voices(self):
        create_dat_folder(AUDIO_PATH)
        self.download_voice(text = 'בבקשה פנו את הדרך', language = 'he-IL', name = 'he-IL-Wavenet-D', file_name = 'VOICE_PLEASE_MOVE_HEBREW')
         
    # Return home
    async def return_home(self):
        
        try:
            await self.preform_navigation(self.home_position['x'], self.home_position['y'], self.home_position['angle'])

        except Exception as e:
            self.log.warn(f'Got error {e}, skipping it. REMOVE THE TRY \ CATCH AFTER BUG IS FIXED')

            # Obstacle management flow                                    
            # self.nav_errors = await self.check_navigation_errors()