
import streamlit as st
import subprocess

st.title('Crypto Dashboard')

st.markdown('---')
st.write('Running your crypto script and showing output:')

try:
    output = subprocess.getoutput('python app.py')
    if not output:
        output = 'No output from script.'
except Exception as e:
    output = f'Error: {e}'

st.code(output)
